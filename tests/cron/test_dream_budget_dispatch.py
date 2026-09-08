"""Cablaggio del budget di memoria e del review pass dentro ``_run_dream``.

Il budget e il review pass sono atterrati inerti nella wave precedente: il
report si calcolava, il guard si costruiva, il prompt del review esisteva, e
nessuno chiamava niente. Qui si verifica la catena che li accende — quando il
review parte, cosa gli arriva, e soprattutto che con i budget al loro default
(0, cioè *misurato ma non applicato*) il flusso resti quello di prima.

Il vicino di casa è ``test_cron_dispatch_dream_cursor.py``, da cui questo file
riprende la forma dei fake; qui però il ``MemoryStore`` finto ha file veri su
disco, perché ``budget_report`` li misura davvero.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.agent.dream_review import STATUS_COMPLETED
from jenny.agent.tools.file_state import FileStates
from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_DREAM_JOB = SimpleNamespace(name="dream", id="job-dream")

_REVIEW_TARGET = "jenny.agent.dream_review.run_dream_review"


def _blocked_writes() -> FileStates:
    """Run che ha tentato di scrivere senza riuscirci: il cursore non avanza.

    Nessun rifiuto **di budget** registrato: è il caso della policy — un path
    fuori dalla allowlist del registry di Dream. Da qui in poi conta la
    differenza (fase 5): liberare spazio non sposta un file che non è scrivibile,
    quindi un review forzato girerebbe a vuoto.
    """
    states = FileStates()
    states.record_write_attempt()
    return states


def _refused_by_budget(path: Path) -> FileStates:
    """Run in cui il tetto ha rifiutato una scrittura, e il contenuto non è
    atterrato: **qui** un review pass ha una leva, perché c'è spazio da liberare."""
    states = FileStates()
    states.record_write_attempt()
    states.record_write_refused(path, "- un fatto che non ci sta")
    return states


def _successful_writes(path: Path) -> FileStates:
    states = FileStates()
    states.record_write_attempt()
    states.record_write(path)
    return states


class _FakeMemory:
    """``MemoryStore`` minimale con file di memoria veri su disco.

    I tre file esistono davvero perché ``budget_report`` ne legge la dimensione:
    un fake che restituisse numeri inventati verificherebbe il cablaggio contro
    se stesso invece che contro il modulo che il codice usa.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        memory_text: str = "m",
        review_state: tuple[int, int] = (0, 0),
        has_work: bool = True,
        file_states: FileStates | None = None,
        events: list[str] | None = None,
    ) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        self.memory_file = workspace / "MEMORY.md"
        self.user_file = workspace / "USER.md"
        self.soul_file = workspace / "SOUL.md"
        self.memory_file.write_text(memory_text, encoding="utf-8")
        self.user_file.write_text("u", encoding="utf-8")
        self.soul_file.write_text("s", encoding="utf-8")

        self._review_state = review_state
        self._forced_at_stuck = 0
        self._nothing_new = 0
        self._has_work = has_work
        self._file_states = file_states
        self.events = events if events is not None else []

        self.cursor: int | None = None
        self.gauges: list[str] = []
        self.guards: list[Any] = []
        self.review_state_writes: list[tuple[int, int]] = []

    # -- lettura/scrittura dello stato del review -----------------------------

    def get_review_state(self) -> tuple[int, int]:
        return self._review_state

    def get_nothing_new_runs(self) -> int:
        return self._nothing_new

    def get_review_forced_at_stuck(self) -> int:
        return self._forced_at_stuck

    def set_review_state(
        self, *, runs_since_review: int, stuck_runs: int,
        forced_at_stuck: int | None = None, nothing_new_runs: int | None = None,
    ) -> None:
        self._review_state = (runs_since_review, stuck_runs)
        if forced_at_stuck is not None:
            self._forced_at_stuck = forced_at_stuck
        if nothing_new_runs is not None:
            self._nothing_new = nothing_new_runs
        self.review_state_writes.append((runs_since_review, stuck_runs))

    # -- turno incrementale ----------------------------------------------------

    def build_dream_prompt(self, *, max_entries: int = 20, gauge: str = ""):
        self.gauges.append(gauge)
        self.events.append("prompt")
        return ("prompt di consolidamento", 42) if self._has_work else None

    def build_dream_tools(self, *, write_size_guard=None, scope="personal"):
        self.guards.append(write_size_guard)
        return SimpleNamespace(file_states=self._file_states)

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.cursor = cursor
        self.events.append(f"cursor:{cursor}")

    def get_last_dream_cursor(self) -> int:
        return 7

    def compact_history(self) -> None:
        pass


class _FakeAgent:
    def __init__(self, sessions_dir: Path, memory: _FakeMemory, stop_reason: str) -> None:
        self.context = SimpleNamespace(memory=memory)
        self.sessions = SimpleNamespace(sessions_dir=sessions_dir)
        self._stop_reason = stop_reason
        self._memory = memory

    async def process_direct(self, prompt: str, **_kwargs):
        self._memory.events.append("process_direct")
        return SimpleNamespace(metadata={"_stop_reason": self._stop_reason}, usage={})

    def evict_pruned_sessions(self, keys) -> None:
        pass


class _ReviewSpy:
    """Sostituto di ``run_dream_review`` che registra cosa gli è arrivato."""

    def __init__(
        self, events: list[str], *, shrink_to: str | None = None, run_turn: bool = False
    ) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []
        self._shrink_to = shrink_to
        self._run_turn = run_turn

    async def __call__(self, agent, *, store, report, snapshotted, write_size_guard=None):
        self.events.append("review")
        self.calls.append({
            "agent": agent,
            "report": report,
            "snapshotted": snapshotted,
            "guard": write_size_guard,
        })
        if self._run_turn:
            # Il review vero fa esattamente questo, ed è l'unico punto in cui
            # nasce la risposta su cui si contabilizzano i token. E come il
            # review vero non rilancia: un turno andato male diventa uno status,
            # non un'eccezione che porta via il resto del tick cron.
            try:
                await agent.process_direct("prompt di review")
            except Exception:
                self.events.append("review-turn-failed")
        before = {item.label: item.chars for item in report}
        if self._shrink_to is not None:
            # Il review pass rimpicciolisce davvero il file: è la condizione in
            # cui un report riusato mentirebbe al turno che segue.
            store.memory_file.write_text(self._shrink_to, encoding="utf-8")
        after = {item.label: len(item.path.read_text(encoding="utf-8")) for item in report}
        return SimpleNamespace(
            status=STATUS_COMPLETED,
            before=before,
            after=after,
            freed=sum(before[label] - after[label] for label in before),
        )

    @property
    def ran(self) -> bool:
        return bool(self.calls)


def _config(
    *, memory_budget: int = 0, review_every_runs: int = 12
) -> Config:
    config = Config()
    dream = config.agents.defaults.dream
    dream.memory_budget_chars = memory_budget
    dream.review_every_runs = review_every_runs
    _ACTIVE_CONFIG["config"] = config
    return config


# Popolato da ``_config()`` e letto dal finto ``load_config`` della fixture
# autouse: è ciò che tiene i test scritti in termini di "questa è la config"
# invece che di "questo è il file su disco".
_ACTIVE_CONFIG: dict[str, Config] = {}


def _dispatcher(
    agent: _FakeAgent, config: Config, snapshot_cb=None
) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: agent,
        config=config,
        cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(),
        snapshot_before_dream=snapshot_cb,
    )


@pytest.fixture(autouse=True)
def _dream_knobs_come_from_disk(monkeypatch: pytest.MonkeyPatch):
    """``_run_dream`` rilegge i knob di Dream da disco a ogni run.

    Non è un dettaglio implementativo da aggirare nel test: è il comportamento,
    ed esiste perché il ``Config`` catturato dal container non viene aggiornato
    da nessuno quando ``/dream budget`` riscrive ``config.json``. Il fake sostituisce
    quindi la lettura, non la scavalca — i test continuano a controllare i knob
    passando il loro ``Config`` a ``_config()``, che questo redirige.
    """
    _ACTIVE_CONFIG.clear()

    def _load() -> Config:
        return _ACTIVE_CONFIG.get("config") or Config()

    monkeypatch.setattr("jenny.config.loader.load_config", _load)


def _install_review(monkeypatch: pytest.MonkeyPatch, spy: _ReviewSpy) -> None:
    monkeypatch.setattr(_REVIEW_TARGET, spy)


# -- trigger del review --------------------------------------------------------


async def test_over_budget_alone_does_not_trigger_a_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sforare il budget non basta a far partire un review, ed è deliberato.

    Sembra il trigger più ovvio dei tre ed è l'unico che non sa fermarsi: un
    file può restare sopra la soglia dopo un review che ha già fatto tutto il
    possibile — il resto è roba che le regole marcano "never delete" — e il
    prompt del review dichiara *valido* un run che non cambia niente. La
    condizione resterebbe vera per sempre e brucerebbe un turno LLM ogni due
    ore, a vuoto, in una feature nata per contenere i costi.

    Chi copre il caso in cui essere sopra budget fa davvero danno è ``stuck``:
    se il tetto blocca una scrittura il cursore non avanza, il contatore sale, e
    due cicli dopo il review parte (v. il test più sotto).
    """
    memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200)
    spy = _ReviewSpy(memory.events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    await _dispatcher(agent, _config(memory_budget=50)).dispatch(_DREAM_JOB)

    assert not spy.ran


async def test_review_skipped_when_nothing_is_over_and_counters_are_low(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 10)
    spy = _ReviewSpy(memory.events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    await _dispatcher(agent, _config(memory_budget=500)).dispatch(_DREAM_JOB)

    assert not spy.ran


async def test_review_runs_on_the_periodic_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nessun file oltre budget, ma sono passati abbastanza run: manutenzione."""
    memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 10, review_state=(4, 0))
    spy = _ReviewSpy(memory.events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    await _dispatcher(agent, _config(memory_budget=500, review_every_runs=4)).dispatch(
        _DREAM_JOB
    )

    assert spy.ran
    # Lo stato riparte da zero: il pass è avvenuto, la cadenza si riazzera.
    assert memory.review_state_writes[0] == (0, 0)
    # E il review precede il turno incrementale: prima si fa spazio, poi si scrive.
    assert memory.events.index("review") < memory.events.index("process_direct")


async def test_two_stuck_runs_force_a_review_on_the_third(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il test che protegge dal livelock.

    Un run che tenta scritture e non ne porta a casa nessuna non avanza il
    cursore — ed è giusto così, il fatto non è stato consolidato e avanzare lo
    perderebbe. Ma senza contatore lo stesso batch tornerebbe al run dopo, con
    lo stesso esito, per sempre: un turno LLM ogni due ore che non consolida
    nulla e, peggio, ``compact_history`` che intanto scarta storia mai
    consolidata. La via d'uscita è forzare il review, non allentare il commit.
    """
    memory = _FakeMemory(
        tmp_path / "ws",
        memory_text="x" * 10,
        file_states=_refused_by_budget(tmp_path / "ws" / "MEMORY.md"),
    )
    spy = _ReviewSpy(memory.events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")
    # Nessun file oltre budget e cadenza lontana: l'unica strada verso il review
    # è il contatore dei run bloccati dal **tetto**.
    dispatcher = _dispatcher(agent, _config(memory_budget=500, review_every_runs=99))

    await dispatcher.dispatch(_DREAM_JOB)
    assert not spy.ran
    assert memory.get_review_state() == (1, 1)

    await dispatcher.dispatch(_DREAM_JOB)
    assert not spy.ran
    assert memory.get_review_state() == (2, 2)

    await dispatcher.dispatch(_DREAM_JOB)
    assert spy.ran


async def test_stuck_counter_resets_on_a_run_that_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = _FakeMemory(
        tmp_path / "ws",
        memory_text="x" * 10,
        review_state=(3, 1),
        file_states=_successful_writes(tmp_path / "written.md"),
    )
    _install_review(monkeypatch, _ReviewSpy(memory.events))
    agent = _FakeAgent(tmp_path, memory, "completed")

    await _dispatcher(agent, _config(memory_budget=500, review_every_runs=99)).dispatch(
        _DREAM_JOB
    )

    assert memory.get_review_state() == (4, 0)


# -- confini del review --------------------------------------------------------


async def test_review_does_not_move_the_dream_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il review pota, non consolida: non ha voci di storia da dichiarare lette.

    Ciclo senza lavoro incrementale, così l'unica cosa che potrebbe muovere il
    cursore è il review — e non deve.
    """
    memory = _FakeMemory(
        tmp_path / "ws", memory_text="x" * 200, has_work=False, review_state=(4, 0)
    )
    spy = _ReviewSpy(memory.events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    await _dispatcher(agent, _config(memory_budget=50, review_every_runs=4)).dispatch(
        _DREAM_JOB
    )

    assert spy.ran
    assert memory.cursor is None
    assert not any(e.startswith("cursor:") for e in memory.events)


async def test_report_and_guard_are_rebuilt_after_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dopo il review i file sono cambiati: misure vecchie non valgono più."""
    memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200, review_state=(4, 0))
    spy = _ReviewSpy(memory.events, shrink_to="x" * 20)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    await _dispatcher(agent, _config(memory_budget=50, review_every_runs=4)).dispatch(
        _DREAM_JOB
    )

    # Il gauge del turno incrementale racconta il file potato (20/50 = 40%), non
    # quello di prima del review (200/50 = 400%).
    gauge = memory.gauges[-1]
    assert "20/50" in gauge
    assert "200/50" not in gauge
    # E il guard non è lo stesso oggetto passato al review: è ricostruito
    # sull'ultima misura, insieme al report da cui nasce.
    assert memory.guards[-1] is not spy.calls[0]["guard"]
    assert memory.guards[-1] is not None


# -- snapshot ------------------------------------------------------------------


async def test_snapshot_precedes_the_review_and_its_outcome_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    memory = _FakeMemory(
        tmp_path / "ws", memory_text="x" * 200, events=events, review_state=(4, 0)
    )
    spy = _ReviewSpy(events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    async def checkpoint() -> bool:
        events.append("snapshot")
        return True

    await _dispatcher(
        agent, _config(memory_budget=50, review_every_runs=4), checkpoint
    ).dispatch(_DREAM_JOB)

    assert events.index("snapshot") < events.index("review")
    assert spy.calls[0]["snapshotted"] is True
    # Un solo checkpoint per ciclo: il turno incrementale che segue è coperto
    # dallo stesso, e un secondo "pre_dream" preso dopo il review non sarebbe
    # pre-niente.
    assert events.count("snapshot") == 1


async def test_missing_snapshot_service_reports_not_snapshotted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Senza checkpoint il prompt del review deve dire "è definitivo"."""
    memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200, review_state=(4, 0))
    spy = _ReviewSpy(memory.events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    await _dispatcher(
        agent, _config(memory_budget=50, review_every_runs=4), None
    ).dispatch(_DREAM_JOB)

    assert spy.calls[0]["snapshotted"] is False


async def test_failed_snapshot_reports_not_snapshotted_and_run_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open, ma senza mentire.

    Uno snapshot che solleva non deve fermare il consolidamento — e non deve
    nemmeno far dire al modello che le sue cancellazioni sono reversibili,
    perché è esattamente la frase che serve a fargliene fare di più.
    """
    events: list[str] = []
    memory = _FakeMemory(
        tmp_path / "ws",
        memory_text="x" * 200,
        file_states=_successful_writes(tmp_path / "written.md"),
        events=events,
        review_state=(4, 0),
    )
    spy = _ReviewSpy(events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    async def broken_checkpoint() -> bool:
        events.append("snapshot")
        raise RuntimeError("checkpoint guasto")

    await _dispatcher(
        agent, _config(memory_budget=50, review_every_runs=4), broken_checkpoint
    ).dispatch(_DREAM_JOB)

    assert spy.calls[0]["snapshotted"] is False
    assert events == ["snapshot", "review", "prompt", "process_direct", "cursor:42"]


# -- spedizione inerte ---------------------------------------------------------


async def test_default_budgets_leave_the_flow_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La rete che dice che la feature si spedisce inerte.

    Con i tre budget a 0 — il default — nessun file può essere ``over``, quindi
    nessun review pass parte, il checkpoint resta uno solo e il cursore avanza
    come ha sempre fatto. L'unica differenza rispetto a prima è ciò che finisce
    nei log e il gauge nel prompt: le misure che serviranno a scegliere i tetti
    veri. Se questo test inizia a fallire, la spedizione non è più inerte.
    """
    events: list[str] = []
    memory = _FakeMemory(
        tmp_path / "ws",
        memory_text="x" * 5000,
        file_states=_successful_writes(tmp_path / "written.md"),
        events=events,
    )
    spy = _ReviewSpy(events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    async def checkpoint() -> bool:
        events.append("snapshot")
        return True

    await _dispatcher(agent, _config(memory_budget=0), checkpoint).dispatch(_DREAM_JOB)

    assert not spy.ran
    assert events == ["prompt", "snapshot", "process_direct", "cursor:42"]
    assert memory.cursor == 42
    # E il guard costruito sui budget a 0 non rifiuta nulla, per quanto grande
    # sia la scrittura: "misurato ma non applicato".
    guard = memory.guards[-1]
    assert guard is not None
    assert guard(memory.memory_file, "y" * 1_000_000) is None


async def test_a_budget_set_after_startup_reaches_the_cron_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I knob di Dream si rileggono da disco, e senza questo la taratura e' inerte.

    ``/dream budget memory 6000`` scrive ``config.json``. Il run manuale lo
    applica subito perche' fa un ``load_config()`` fresco; questo run —- quello
    che gira ogni due ore ed e' il consumatore vero -— userebbe il ``Config``
    catturato quando il container si e' costruito, che **nessuno aggiorna**:
    ``_on_settings_changed`` ricarica modello e provider, non questo. L'utente
    vedrebbe il budget confermato in chat e nessun effetto fino al riavvio.

    Il test simula esattamente quella sequenza: dispatcher costruito con budget
    a 0, poi il file cambia, poi il run parte.
    """
    memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200)
    spy = _ReviewSpy(memory.events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")

    startup_config = _config(memory_budget=0)
    dispatcher = _dispatcher(agent, startup_config)

    # Dopo la costruzione qualcuno scrive un budget: e' cio' che fa `/dream budget`.
    tuned = Config()
    tuned.agents.defaults.dream.memory_budget_chars = 50
    tuned.agents.defaults.dream.review_every_runs = 12
    _ACTIVE_CONFIG["config"] = tuned

    await dispatcher.dispatch(_DREAM_JOB)

    # Il gauge del turno incrementale porta il numero nuovo, non lo zero di
    # avvio: con lo zero MEMORY.md sarebbe reso come "no budget" come gli altri
    # due, che restano a 0 e sono infatti la controprova nella stessa riga.
    gauge = memory.gauges[-1]
    assert "MEMORY.md [400% — 200/50 chars]" in gauge, gauge
    assert "MEMORY.md [200 chars — no budget]" not in gauge, gauge
    # E il Config di avvio e' rimasto quello che era: non lo stiamo mutando,
    # lo stiamo scavalcando con la lettura da disco.
    assert startup_config.agents.defaults.dream.memory_budget_chars == 0


async def test_a_write_the_policy_blocked_does_not_force_a_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fase 5: i due contatori hanno rimedi diversi, quindi sono due contatori.

    Una scrittura fermata dalla **policy** — un path fuori dalla allowlist del
    registry di Dream — non ha niente a che vedere con lo spazio: liberarne non
    rende scrivibile quel file. Prima saliva lo stesso contatore del tetto e due
    run dopo partiva un review pass che non poteva servire a niente. Misurato sul
    Titan 2 il 2026-08-18 nella sua variante gemella: un review forzato su file al
    77% e 79%, senza niente da liberare.
    """
    memory = _FakeMemory(
        tmp_path / "ws", memory_text="x" * 10, file_states=_blocked_writes()
    )
    spy = _ReviewSpy(memory.events)
    _install_review(monkeypatch, spy)
    agent = _FakeAgent(tmp_path, memory, "completed")
    dispatcher = _dispatcher(agent, _config(memory_budget=500, review_every_runs=99))

    for _ in range(3):
        await dispatcher.dispatch(_DREAM_JOB)

    assert not spy.ran
    # Il contatore che forza il review resta fermo; sale l'altro.
    assert memory.get_review_state()[1] == 0
    assert memory.get_nothing_new_runs() == 3
