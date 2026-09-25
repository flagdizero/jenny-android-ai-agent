"""L'heartbeat può ammettere che un suo task non è partito (B13).

B8 aveva dato il terzo esito ai monitor, ma il guasto osservato sul telefono era
un task di ``HEARTBEAT.md``: il controllo delle piante girava di lì. La
differenza è la granularità — un run copre N task, e un contatore solo per tutto
il file direbbe "l'heartbeat è rotto" mentre tre quarti funziona.

Il vincolo che questi test difendono, nell'ordine:

1. **Un giro senza novità non parla, e non vede un prompt diverso.** È il valore
   dell'heartbeat: costa zero quando non c'è niente da dire.
2. Un task saltato *perché le sue istruzioni dicevano di saltarlo in silenzio*
   ha fatto quello che doveva: non è un guasto (il task WaterBot reale dice "se
   hps è irraggiungibile salta il ciclo in silenzio").
3. Tre fallimenti di fila di **un** task producono **un** messaggio, che nomina
   quel task e non gli altri.

L'agente finto qui sotto non recita un copione: legge il prompt e si comporta
come il contratto gli chiede, quindi il test misura davvero la catena
prompt → marcatore → stato per-task → prompt del giro dopo.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from support.sessions import FakeSessions

from jenny.agent.turn_types import TurnOutcome
from jenny.cron.could_not_check import ESCALATE_AFTER_FAILURES
from jenny.cron.heartbeat_tasks import (
    active_section_text,
    parse_heartbeat_tasks,
    task_index_block,
)
from jenny.cron.service import CronService
from jenny.cron.types import CronJob, CronJobState, CronPayload, CronSchedule
from jenny.runtime.cron_dispatch import _HEARTBEAT_PREAMBLE, CronDispatcher

_WATERBOT = (
    "- Ogni ciclo, controlla l'umidità delle piante e avvisami solo se una è sotto il 15%. "
    "Se hps è irraggiungibile salta il ciclo in silenzio."
)
_VITAMINS = "- Alle 9 ricordami le vitamine."

_ESCALATION_HEAD = "These recurring tasks have now failed to run"


def _heartbeat_md(*tasks: str) -> str:
    body = "\n".join(tasks)
    return f"# Heartbeat Tasks\n\n## Active Tasks\n\n{body}\n"


def _healthy_prompt(content: str) -> str:
    """Il prompt di un heartbeat sano, ricostruito pezzo per pezzo.

    Serve a due cose: dare all'agente finto un modo di sapere se questo giro gli
    sta chiedendo di parlare (come fa il test dei monitor in B8), e fissare la
    forma del prompt che un giro sano vede — che è l'invariante da difendere.
    """
    return (
        _HEARTBEAT_PREAMBLE
        + "Review the following HEARTBEAT.md and report any active tasks:\n\n"
        + active_section_text(content)
        + "\n"
        + task_index_block(parse_heartbeat_tasks(content))
    )


def _escalated_labels(prompt: str) -> list[str]:
    """Le etichette dei task che il prompt sta chiedendo di riferire."""
    lines = prompt.splitlines()
    start = next(i for i, line in enumerate(lines) if _ESCALATION_HEAD in line)
    labels: list[str] = []
    for line in lines[start + 1:]:
        if not line.startswith("- "):
            break
        labels.append(line.split(". ", 1)[1])
    return labels


def _escalated_numbers(prompt: str) -> list[int]:
    """I numeri degli stessi task, per scrivere la riga ``CHECK_WARNED``.

    È quella riga a registrare l'avviso: lo stato non lo deduce più dal fatto che
    un ``message`` sia uscito, perché quel fatto è vero anche per un messaggio
    che parlava d'altro.
    """
    lines = prompt.splitlines()
    start = next(i for i, line in enumerate(lines) if _ESCALATION_HEAD in line)
    numbers: list[int] = []
    for line in lines[start + 1:]:
        if not line.startswith("- "):
            break
        numbers.append(int(line[2:].split(".", 1)[0]))
    return numbers


class _FakeHeartbeatAgent:
    """Agente che *segue il prompt* invece di eseguire un copione.

    - ``broken``: numero del task → motivo per cui non è partito. Quei task
      producono la riga di marcatore, che non raggiunge nessuno.
    - ``silently_skipped``: task che l'agente non esegue **perché le loro stesse
      istruzioni glielo dicono**. Nessun marcatore, nessun messaggio: ha fatto
      quello che gli era stato chiesto.
    - Parla (tool ``message``) **solo** se il prompt di questo giro glielo
      chiede, e allora scrive un messaggio solo con i task che il prompt elenca.
    """

    def __init__(self) -> None:
        self.sessions = FakeSessions()
        self.prompts: list[str] = []
        self.messages: list[str] = []
        self.broken: dict[int, str] = {}
        self.silently_skipped: set[int] = set()
        self.skips = 0
        self.omits_the_task_number = False
        self.obeys_the_escalation = True

    async def process_direct_outcome(self, prompt: str, **_kwargs: Any) -> TurnOutcome:
        self.prompts.append(prompt)
        # Uno skip istruito non produce niente: non un messaggio, non un
        # marcatore. Contato solo perché il test possa dire che è avvenuto.
        self.skips += len(self.silently_skipped)
        lines = [
            f"CHECK_FAILED{'' if self.omits_the_task_number else f' {number}'}: {reason}"
            for number, reason in sorted(self.broken.items())
        ]
        if _ESCALATION_HEAD in prompt and self.broken and self.obeys_the_escalation:
            labels = _escalated_labels(prompt)
            self.messages.append("Non riesco più a eseguire: " + ", ".join(labels))
            # Il prompt chiede il messaggio **e** la riga che lo registra.
            lines += [
                f"CHECK_WARNED {number}"
                for number in _escalated_numbers(prompt)
                if number in self.broken
            ]
            return TurnOutcome.spoke_via_tool(final_text="\n".join(lines))
        return TurnOutcome.silent(final_text="\n".join(lines))

    def evict_pruned_sessions(self, keys: list[str]) -> None:  # pragma: no cover
        pass


class _Harness:
    """``CronService`` vero + ``CronDispatcher`` vero: lo stato passa dal disco."""

    def __init__(self, tmp_path: Path, content: str) -> None:
        self.workspace = tmp_path
        self.file = tmp_path / "HEARTBEAT.md"
        self.file.write_text(content, encoding="utf-8")
        self.store_path = tmp_path / "cron" / "jobs.json"
        self.agent = _FakeHeartbeatAgent()
        self.service = CronService(self.store_path)
        self.service.on_job = CronDispatcher(
            get_agent=lambda: self.agent,
            config=SimpleNamespace(workspace_path=tmp_path),
            cron=self.service,
            heartbeat_cfg=SimpleNamespace(keep_recent_messages=8),
        ).dispatch
        self.service.register_system_job(
            CronJob(
                id="heartbeat",
                name="heartbeat",
                schedule=CronSchedule(kind="every", every_ms=1_800_000),
                payload=CronPayload(kind="system_event"),
            )
        )

    def rewrite(self, content: str) -> None:
        self.file.write_text(content, encoding="utf-8")

    async def cycles(self, count: int) -> None:
        for _ in range(count):
            await self.service.run_job("heartbeat")

    @property
    def state(self) -> CronJobState:
        job = self.service.get_job("heartbeat")
        assert job is not None
        return job.state


@pytest.fixture
def two_tasks(tmp_path: Path) -> _Harness:
    return _Harness(tmp_path, _heartbeat_md(_WATERBOT, _VITAMINS))


class TestSilenceStaysFree:
    """Il caso che non deve regredire: un heartbeat sano non dice niente."""

    async def test_a_healthy_heartbeat_never_says_anything(self, two_tasks: _Harness) -> None:
        await two_tasks.cycles(10)

        assert two_tasks.agent.messages == []
        assert two_tasks.state.last_status == "ok"
        assert two_tasks.state.task_checks == {}

    async def test_a_healthy_heartbeat_sees_the_same_prompt_every_time(
        self, two_tasks: _Harness
    ) -> None:
        """Nessuna riga in più finché non c'è un guasto."""
        await two_tasks.cycles(5)

        expected = _healthy_prompt(two_tasks.file.read_text(encoding="utf-8"))
        assert two_tasks.agent.prompts == [expected] * 5
        assert _ESCALATION_HEAD not in expected

    async def test_an_instructed_silent_skip_is_not_a_failure(
        self, two_tasks: _Harness
    ) -> None:
        """Il task WaterBot dice "se hps è irraggiungibile salta il ciclo in
        silenzio": saltare è ciò che gli è stato chiesto, e non deve contare come
        un controllo mancato — neanche dopo dieci cicli."""
        two_tasks.agent.silently_skipped = {1}

        await two_tasks.cycles(10)

        assert two_tasks.agent.skips == 10
        assert two_tasks.agent.messages == []
        assert two_tasks.state.task_checks == {}
        assert two_tasks.state.last_status == "ok"

    async def test_a_single_failed_task_produces_no_message(self, two_tasks: _Harness) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}

        await two_tasks.cycles(1)

        assert two_tasks.agent.messages == []
        assert two_tasks.state.last_status == "could_not_check"

    async def test_two_failed_runs_are_still_within_the_margin(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES - 1)

        assert two_tasks.agent.messages == []


class TestTheStreakSpeaksOncePerTask:
    async def test_three_failures_of_one_task_produce_exactly_one_message(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert len(two_tasks.agent.messages) == 1

    async def test_the_message_names_the_broken_task(self, two_tasks: _Harness) -> None:
        """"Il controllo delle piante non parte" è utile; "l'heartbeat è rotto" no."""
        two_tasks.agent.broken = {1: "hps non raggiungibile"}

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert "controlla l'umidità delle piante" in two_tasks.agent.messages[0]

    async def test_the_healthy_task_in_the_same_run_is_never_mentioned(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert "vitamine" not in two_tasks.agent.messages[0]
        # E il task sano non ha lasciato traccia nello stato.
        tasks = parse_heartbeat_tasks(two_tasks.file.read_text(encoding="utf-8"))
        assert list(two_tasks.state.task_checks) == [tasks[0].id]

    async def test_two_tasks_broken_at_once_cost_one_message_listing_both(
        self, two_tasks: _Harness
    ) -> None:
        """N task che cadono insieme — di solito per la stessa causa — devono
        costare un'interruzione sola, non N."""
        two_tasks.agent.broken = {1: "hps non raggiungibile", 2: "sveglia non impostata"}

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert len(two_tasks.agent.messages) == 1
        assert "umidità delle piante" in two_tasks.agent.messages[0]
        assert "vitamine" in two_tasks.agent.messages[0]

    async def test_the_alert_is_not_repeated_while_the_task_stays_broken(
        self, two_tasks: _Harness
    ) -> None:
        """Un guasto che dura un giorno deve costare un messaggio, non quarantotto."""
        two_tasks.agent.broken = {1: "hps non raggiungibile"}

        await two_tasks.cycles(12)

        assert len(two_tasks.agent.messages) == 1
        tasks = parse_heartbeat_tasks(two_tasks.file.read_text(encoding="utf-8"))
        entry = two_tasks.state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == 12
        assert entry.escalated is True

    async def test_the_run_that_alerted_still_counts_as_a_missed_check(
        self, two_tasks: _Harness
    ) -> None:
        """Legare l'esito a "ha parlato" azzererebbe la sequenza proprio lì, e
        l'avviso tornerebbe ogni tre cicli per sempre."""
        two_tasks.agent.broken = {1: "hps non raggiungibile"}

        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        tasks = parse_heartbeat_tasks(two_tasks.file.read_text(encoding="utf-8"))
        entry = two_tasks.state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == ESCALATE_AFTER_FAILURES

    async def test_a_model_that_ignores_the_instruction_is_asked_again(
        self, two_tasks: _Harness
    ) -> None:
        """L'avviso è "dato" solo se è uscito davvero."""
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        two_tasks.agent.obeys_the_escalation = False

        await two_tasks.cycles(5)

        tasks = parse_heartbeat_tasks(two_tasks.file.read_text(encoding="utf-8"))
        assert two_tasks.state.task_checks[tasks[0].id].escalated is False
        assert _ESCALATION_HEAD in two_tasks.agent.prompts[-1]

    async def test_a_task_that_starts_working_again_stops_the_streak(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        two_tasks.agent.broken = {}
        await two_tasks.cycles(1)

        assert two_tasks.state.task_checks == {}
        assert two_tasks.state.last_status == "ok"
        # Il ritorno alla normalità non è una notizia.
        assert len(two_tasks.agent.messages) == 1

    async def test_a_second_outage_can_alert_again(self, two_tasks: _Harness) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)
        two_tasks.agent.broken = {}
        await two_tasks.cycles(1)
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        assert len(two_tasks.agent.messages) == 2

    async def test_the_heartbeat_stays_armed_while_a_task_is_broken(
        self, two_tasks: _Harness
    ) -> None:
        """Non riuscire a eseguire un task non è un motivo per smettere di provarci."""
        two_tasks.agent.broken = {1: "hps non raggiungibile"}

        await two_tasks.cycles(4)

        job = two_tasks.service.get_job("heartbeat")
        assert job is not None
        assert job.enabled is True
        assert job.state.next_run_at_ms is not None


class TestTheFileKeepsChanging:
    """``HEARTBEAT.md`` è un file che l'utente riscrive quando vuole."""

    async def test_a_reworded_task_does_not_inherit_the_streak(
        self, tmp_path: Path
    ) -> None:
        """Riscrivere un task lo rende un task nuovo: la sequenza riparte da zero.

        È il modo di sbagliare scelto: un avviso in ritardo di K cicli è meglio
        di un avviso che parla di un controllo che l'utente ha appena cambiato.
        """
        harness = _Harness(tmp_path, _heartbeat_md(_WATERBOT))
        harness.agent.broken = {1: "hps non raggiungibile"}
        await harness.cycles(ESCALATE_AFTER_FAILURES - 1)

        harness.rewrite(_heartbeat_md(_WATERBOT + " Soglia: 20%."))
        await harness.cycles(1)

        assert harness.agent.messages == []
        tasks = parse_heartbeat_tasks(harness.file.read_text(encoding="utf-8"))
        assert harness.state.task_checks[tasks[0].id].consecutive_could_not_check == 1

    async def test_state_for_a_deleted_task_is_pruned(self, two_tasks: _Harness) -> None:
        """Altrimenti lo store cresce a ogni task che l'utente cancella."""
        two_tasks.agent.broken = {1: "hps non raggiungibile", 2: "sveglia non impostata"}
        await two_tasks.cycles(1)
        assert len(two_tasks.state.task_checks) == 2

        two_tasks.rewrite(_heartbeat_md(_VITAMINS))
        two_tasks.agent.broken = {1: "sveglia non impostata"}
        await two_tasks.cycles(1)

        tasks = parse_heartbeat_tasks(two_tasks.file.read_text(encoding="utf-8"))
        assert list(two_tasks.state.task_checks) == [tasks[0].id]

    async def test_reordering_the_file_keeps_the_streak(self, two_tasks: _Harness) -> None:
        """L'identità è il testo del task, non la sua posizione."""
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES - 1)

        two_tasks.rewrite(_heartbeat_md(_VITAMINS, _WATERBOT))
        two_tasks.agent.broken = {2: "hps non raggiungibile"}
        await two_tasks.cycles(1)

        assert len(two_tasks.agent.messages) == 1
        assert "umidità delle piante" in two_tasks.agent.messages[0]

    async def test_an_unnumbered_marker_with_several_tasks_blames_nobody(
        self, two_tasks: _Harness
    ) -> None:
        """Attribuirlo a caso produrrebbe un avviso su un controllo sano."""
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        two_tasks.agent.omits_the_task_number = True

        await two_tasks.cycles(6)

        assert two_tasks.agent.messages == []
        assert two_tasks.state.task_checks == {}
        # Il run resta comunque registrato come "non ho potuto controllare".
        assert two_tasks.state.last_status == "could_not_check"

    async def test_with_a_single_task_the_number_can_be_left_out(
        self, tmp_path: Path
    ) -> None:
        """Un file con un task solo non ha niente da distinguere: chiedere il
        numero sarebbe solo un'occasione di sbagliarlo."""
        harness = _Harness(tmp_path, _heartbeat_md(_WATERBOT))
        harness.agent.broken = {1: "hps non raggiungibile"}
        harness.agent.omits_the_task_number = True

        await harness.cycles(ESCALATE_AFTER_FAILURES)

        assert len(harness.agent.messages) == 1


class TestPersistedState:
    """Ciò che un lettore esterno (WebUI, tool ``cron``) troverà nello store."""

    async def test_the_streak_survives_a_restart(self, two_tasks: _Harness) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        await two_tasks.cycles(2)

        reloaded = CronService(two_tasks.store_path).get_job("heartbeat")

        assert reloaded is not None
        tasks = parse_heartbeat_tasks(two_tasks.file.read_text(encoding="utf-8"))
        entry = reloaded.state.task_checks[tasks[0].id]
        assert entry.consecutive_could_not_check == 2
        assert entry.escalated is False
        assert entry.since_ms is not None
        assert entry.label.startswith("Ogni ciclo")

    async def test_a_restart_does_not_re_alert_a_task_already_reported(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        restarted = _Harness(two_tasks.workspace, two_tasks.file.read_text(encoding="utf-8"))
        restarted.agent.broken = {1: "hps non raggiungibile"}
        await restarted.cycles(4)

        assert restarted.agent.messages == []

    async def test_the_state_is_written_in_camel_case_like_the_rest(
        self, two_tasks: _Harness
    ) -> None:
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        await two_tasks.cycles(ESCALATE_AFTER_FAILURES)

        raw = json.loads(two_tasks.store_path.read_text(encoding="utf-8"))
        state = next(j for j in raw["jobs"] if j["id"] == "heartbeat")["state"]
        (entry,) = state["taskChecks"].values()
        assert entry["consecutiveCouldNotCheck"] == ESCALATE_AFTER_FAILURES
        assert entry["escalated"] is True
        assert isinstance(entry["sinceMs"], int)
        assert entry["label"].startswith("Ogni ciclo")

    async def test_the_run_summary_says_which_task_and_why(
        self, two_tasks: _Harness
    ) -> None:
        """La risposta a "il controllo delle piante funziona?" senza aprire logcat."""
        two_tasks.agent.broken = {1: "hps non raggiungibile"}
        await two_tasks.cycles(1)

        assert two_tasks.state.last_status == "could_not_check"
        assert "hps non raggiungibile" in (two_tasks.state.last_error or "")
        assert [r.status for r in two_tasks.state.run_history] == ["could_not_check"]
