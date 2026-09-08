"""Il ciclo condiviso di Dream, e la prova che i suoi due chiamanti restano allineati.

``jenny/agent/dream_cycle.py`` esiste perché un run di Dream era implementato due
volte — il job cron e lo slash command ``/dream`` — e le due copie divergevano una
divergenza alla volta: il guard del budget montato solo di là, il gauge assente di
qua, i contatori del review che non avanzavano lanciando Dream a mano. Ognuna è
stata trovata e allineata a mano, ed è l'allineamento a mano la ragione per cui ne
sarebbe arrivata un'altra.

Questo file fa due mestieri distinti, e il secondo è quello per cui il primo vale
la pena:

1. prova il modulo condiviso una volta sola — trigger del review, checkpoint,
   ricostruzione delle misure, aritmetica dei contatori;
2. fa girare **i due chiamanti veri** sullo stesso stato e confronta ciò che si
   osserva da fuori: la traccia delle chiamate sullo store e le righe di log che
   il ciclo emette. È il test che fallisce il giorno in cui uno dei due percorsi
   ricomincia a fare storia a sé.

I vicini di casa sono ``tests/cron/test_dream_budget_dispatch.py`` e
``tests/command/test_dream_run_budget.py``, che continuano a coprire ciascuno il
proprio percorso con i propri fake: qui si guarda solo ciò che i due devono avere
in comune.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from jenny.agent.dream_cycle import (
    REVIEW_RETRY_AFTER_RUNS,
    STUCK_FORCES_REVIEW,
    STUCK_IS_ALARMING,
    begin_dream_cycle,
    finish_dream_cycle,
    format_budget,
    format_stuck_alarm,
    take_dream_snapshot,
)
from jenny.agent.dream_review import STATUS_COMPLETED, STATUS_FAILED
from jenny.agent.tools.file_state import FileStates
from jenny.bus.events import InboundMessage
from jenny.command.builtin import register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher
from jenny.runtime.notifier import alert_fields
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY

_REVIEW_TARGET = "jenny.agent.dream_review.run_dream_review"

_DREAM_JOB = SimpleNamespace(name="dream", id="job-dream")

# Scrittura enorme con cui si interroga il guard montato su un run: serve solo a
# sapere se l'enforcement è acceso, senza dover ispezionare l'oggetto.
_HUGE_WRITE = "y" * 1_000_000


# ---------------------------------------------------------------------------
# Fake condivisi dai due percorsi
# ---------------------------------------------------------------------------


class _FakeMemory:
    """``MemoryStore`` minimale con i tre file di memoria veri su disco.

    Veri perché ``budget_report`` li misura davvero: un fake che restituisse
    numeri inventati verificherebbe il cablaggio contro se stesso.

    Ogni chiamata che il ciclo fa sullo store finisce in ``events``, in ordine.
    È quella lista, e non un conteggio di invocazioni, la forma osservabile su
    cui i due percorsi vengono confrontati: dice anche *quando* le cose sono
    successe, che è metà di ciò che qui può divergere.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        memory_text: str = "m",
        review_state: tuple[int, int] = (0, 0),
        has_work: bool = True,
        file_states: FileStates | None = None,
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

        self.events: list[str] = []
        self.gauges: list[str] = []
        self.guards: list[Any] = []
        self.cursor: int | None = None

    @staticmethod
    def _review_counter(value: int | None) -> int:
        return 0 if value is None or value < 0 else value

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
        # ``None`` conserva il valore su disco, come il vero ``MemoryStore``: un
        # doppio che lo azzerasse per omissione renderebbe verde il livelock che
        # ``forced_at_stuck`` esiste per chiudere.
        if forced_at_stuck is not None:
            self._forced_at_stuck = forced_at_stuck
        # E ``stuck_runs=0`` lo azzera comunque, sempre come il vero store: quel
        # campo vale solo dentro la salita di ``stuck`` che lo ha prodotto. Un doppio
        # che se lo tenesse renderebbe verde il difetto opposto — il review che non
        # riparte più alla salita dopo.
        if self._review_counter(stuck_runs) == 0:
            self._forced_at_stuck = 0
        if nothing_new_runs is not None:
            self._nothing_new = nothing_new_runs
        self.events.append(f"review_state:{runs_since_review},{stuck_runs}")

    def build_dream_prompt(self, *, max_entries: int = 20, gauge: str = ""):
        self.gauges.append(gauge)
        self.events.append(f"prompt:{gauge.splitlines()[1] if gauge else ''}")
        return ("prompt di consolidamento", 42) if self._has_work else None

    def build_dream_tools(self, *, write_size_guard=None, scope="personal"):
        self.guards.append(write_size_guard)
        refuses = (
            write_size_guard is not None
            and write_size_guard(self.memory_file, _HUGE_WRITE) is not None
        )
        self.events.append(f"tools:refuses={refuses}")
        return SimpleNamespace(file_states=self._file_states)

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.cursor = cursor
        self.events.append(f"cursor:{cursor}")

    def get_last_dream_cursor(self) -> int:
        return 7

    def compact_history(self) -> None:
        pass


class _FakeAgent:
    """Il minimo comune dei due chiamanti: agente del cron e loop del comando.

    Uno solo per entrambi non è pigrizia — è ciò che rende il confronto un
    confronto: se i due percorsi ricevessero fake diversi, una differenza fra i
    fake basterebbe a spiegare (o a nascondere) una differenza fra i percorsi.
    """

    def __init__(
        self,
        workspace: Path,
        memory: _FakeMemory,
        *,
        snapshot_before_dream: Any = None,
        turn_explodes: bool = False,
    ) -> None:
        self.context = SimpleNamespace(memory=memory, timezone=None)
        self.sessions = SimpleNamespace(sessions_dir=workspace / "sessions")
        self.bus = SimpleNamespace(publish_outbound=self._publish)
        self.published: list[Any] = []
        self.snapshot_before_dream = snapshot_before_dream
        self.turn_explodes = turn_explodes
        self._memory = memory

    async def _publish(self, message: Any) -> None:
        self.published.append(message)

    async def process_direct(self, prompt: str, **_kwargs: Any):
        self._memory.events.append("process_direct")
        if self.turn_explodes:
            # Un turno che solleva non è ipotetico: provider giù, token esauriti,
            # processo ucciso a metà. Ciò che conta è che il ciclo si chiuda
            # comunque, o la cadenza del review non avanza mai.
            raise RuntimeError("il provider non risponde")
        return SimpleNamespace(metadata={"_stop_reason": "completed"}, usage={})

    def evict_pruned_sessions(self, _keys: Any) -> None:
        pass


class _ReviewSpy:
    """Sostituto di ``run_dream_review`` che registra cosa gli è arrivato."""

    def __init__(
        self,
        memory: _FakeMemory,
        *,
        shrink_to: str | None = None,
        status: str = STATUS_COMPLETED,
    ) -> None:
        self._memory = memory
        self._shrink_to = shrink_to
        self._status = status
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, agent, *, store, report, snapshotted, write_size_guard=None):
        self._memory.events.append(f"review:snapshotted={snapshotted}")
        self.calls.append({
            "agent": agent,
            "report": report,
            "snapshotted": snapshotted,
            "guard": write_size_guard,
        })
        before = {item.label: item.chars for item in report}
        if self._shrink_to is not None:
            store.memory_file.write_text(self._shrink_to, encoding="utf-8")
        after = {item.label: len(item.path.read_text(encoding="utf-8")) for item in report}
        return SimpleNamespace(
            status=self._status,
            before=before,
            after=after,
            freed=sum(before[label] - after[label] for label in before),
            # Come il vero ``ReviewOutcome``: `/dream` lo legge per dire *quali*
            # fatti la passata ha spostato in archivio, non solo quanti caratteri
            # ha liberato.
            demoted_ids=(),
        )

    @property
    def ran(self) -> bool:
        return bool(self.calls)


def _blocked_writes() -> FileStates:
    """Run che ha tentato di scrivere senza riuscirci: il cursore non avanza."""
    states = FileStates()
    states.record_write_attempt()
    return states


def _successful_writes(path: Path) -> FileStates:
    states = FileStates()
    states.record_write_attempt()
    states.record_write(path)
    return states


def _config(*, memory_budget: int = 0, review_every_runs: int = 12) -> Config:
    config = Config()
    dream = config.agents.defaults.dream
    dream.memory_budget_chars = memory_budget
    dream.review_every_runs = review_every_runs
    return config


def _dream_cfg(*, memory_budget: int = 0, review_every_runs: int = 12):
    return _config(
        memory_budget=memory_budget, review_every_runs=review_every_runs
    ).agents.defaults.dream


@contextmanager
def _cycle_logs() -> Iterator[list[str]]:
    """Raccogli le righe emesse **dal modulo condiviso**, e solo quelle.

    Sono la parte di comportamento che non passa dallo store: la riga del budget
    a ogni run (punto 7 del catalogo) e l'ERROR sul livelock conclamato (punto
    6). Erano entrambe solo lato cron, e senza guardarle il confronto fra i due
    percorsi le lascerebbe divergere di nuovo in silenzio.
    """
    messages: list[str] = []
    handler = logger.add(
        lambda m: messages.append(f"{m.record['level'].name}: {m.record['message']}"),
        level="DEBUG",
        filter=lambda record: record["name"] == "jenny.agent.dream_cycle",
    )
    try:
        yield messages
    finally:
        logger.remove(handler)


# ---------------------------------------------------------------------------
# 1. Il modulo condiviso
# ---------------------------------------------------------------------------


class TestReviewTrigger:
    async def test_over_budget_alone_does_not_trigger_a_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sforare il budget non basta a far partire un review, ed è deliberato.

        Sembra il trigger più ovvio dei tre ed è l'unico che non sa fermarsi: un
        file può restare sopra la soglia dopo un review che ha già fatto tutto il
        possibile — il resto è roba che le regole marcano "never delete" — e il
        prompt del review dichiara *valido* un run che non cambia niente. La
        condizione resterebbe vera per sempre e brucerebbe un turno LLM ogni due
        ore. Chi copre il caso in cui essere sopra budget fa davvero danno è
        ``stuck``.
        """
        memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200)
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(memory_budget=50),
        )

        assert not spy.ran
        assert prologue.review is None

    async def test_the_periodic_cadence_fires_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
        )

        assert spy.ran
        assert prologue.review is not None
        # I contatori sono azzerati sia su disco sia nel prologo che il chiamante
        # passerà a ``finish_dream_cycle``: rileggerli lì sarebbe un secondo
        # accesso a uno stato che nel frattempo un altro run può aver riscritto.
        assert memory.get_review_state() == (0, 0)
        assert (prologue.runs_since_review, prologue.stuck) == (0, 0)

    async def test_stuck_runs_force_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L'uscita di emergenza dal livelock, con la cadenza periodica lontana."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(0, STUCK_FORCES_REVIEW))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=99),
        )

        assert spy.ran

    async def test_report_and_guard_are_rebuilt_after_the_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dopo il review i file sono cambiati: misure vecchie non valgono più."""
        memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200, review_state=(4, 0))
        spy = _ReviewSpy(memory, shrink_to="x" * 20)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(memory_budget=50, review_every_runs=4),
        )

        # Il report che il chiamante userà per il gauge descrive il file potato.
        assert [item.chars for item in prologue.report if item.label == "MEMORY.md"] == [20]
        # E il guard non è quello passato al review: nasce dall'ultima misura,
        # insieme al report da cui deriva.
        assert prologue.guard is not spy.calls[0]["guard"]
        assert prologue.guard(memory.memory_file, "z" * 300) is not None


class TestTheForcedReviewDoesNotRepeatOnAFrozenCounter:
    """``stuck`` fermo non deve far partire un review a ogni run.

    Il ramo del livelock scatta su ``stuck % STUCK_FORCES_REVIEW == 0``, e
    ``stuck`` **non** viene toccato quando non c'era storia da consolidare
    (``advanced is None`` in ``finish_dream_cycle``) — cioè su ogni installazione
    in pari, che è lo stato normale di un telefono che Dream ha già digerito.
    Fermo su un multiplo della soglia, quella condizione è vera per sempre: un
    turno LLM di review ogni due ore, a vuoto, su file che nessuno sta toccando.
    Lo specchio esatto del livelock che il contatore esiste per chiudere, su una
    feature il cui scopo è contenere i costi.
    """

    async def test_a_frozen_stuck_forces_exactly_one_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(0, STUCK_FORCES_REVIEW))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)
        agent = _FakeAgent(tmp_path, memory)

        for _ in range(3):
            await begin_dream_cycle(agent, store=memory, cfg=_dream_cfg(review_every_runs=99))
            # Nessuna storia da consolidare: ``stuck`` resta dov'è, ed è
            # esattamente la condizione che rendeva il trigger permanente.
            finish_dream_cycle(
                memory, advanced=None, runs_since_review=0, stuck=STUCK_FORCES_REVIEW
            )

        assert len(spy.calls) == 1

    async def test_a_stuck_that_grows_forces_it_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il meccanismo resta vivo: è "non riforzare sullo stesso valore", non
        "non riforzare più"."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(0, STUCK_FORCES_REVIEW))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)
        agent = _FakeAgent(tmp_path, memory)
        cfg = _dream_cfg(review_every_runs=99)

        await begin_dream_cycle(agent, store=memory, cfg=cfg)
        # Due run bloccati in più: il contatore avanza e riattraversa la soglia.
        memory.set_review_state(runs_since_review=0, stuck_runs=STUCK_FORCES_REVIEW * 2)
        await begin_dream_cycle(agent, store=memory, cfg=cfg)

        assert len(spy.calls) == 2

    async def test_the_periodic_cadence_is_untouched_by_the_memory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un review periodico non dice niente sul livelock e non lo registra."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, STUCK_FORCES_REVIEW))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory), store=memory, cfg=_dream_cfg(review_every_runs=4)
        )

        assert memory.get_review_forced_at_stuck() == STUCK_FORCES_REVIEW


class TestTheForcedReviewRearmsAfterTheCursorMoves:
    """Il freno del livelock non deve diventare un blocco.

    ``forced_at_stuck`` impedisce di riforzare il review sullo stesso valore di
    ``stuck``, ed è giusto finché quella salita dura. Se sopravvive all'episodio,
    la salita dopo ritrova ``stuck == forced_at`` e la condizione
    ``stuck != forced_at`` è falsa proprio al run in cui il review servirebbe: la
    via d'uscita dal livelock si arma una volta per installazione. Misurato sul
    Titan 2 il 2026-08-18 — ``forced_at_stuck: 2`` avanzato da un episodio già
    chiuso, ``stuck`` di nuovo a 2, nessun review; è arrivato solo a 4, cioè con
    ``STUCK_IS_ALARMING``, quando il danno era già fatto.
    """

    @staticmethod
    async def _cycle(agent, memory, cfg, *, advanced: bool) -> None:
        """Un run intero, nell'ordine del chiamante vero (v. ``cron_dispatch``)."""
        prologue = await begin_dream_cycle(agent, store=memory, cfg=cfg)
        finish_dream_cycle(
            memory,
            advanced=advanced,
            runs_since_review=prologue.runs_since_review,
            stuck=prologue.stuck,
            nothing_new=prologue.nothing_new,
            # Il ramo del tetto: è quello che fa salire ``stuck`` e quindi l'unico
            # che porta al review forzato di cui parla questa classe (fase 5).
            refused=0 if advanced else 1,
        )

    async def test_a_second_climb_forces_a_second_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws")
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)
        agent = _FakeAgent(tmp_path, memory)
        # Cadenza periodica fuori portata: qui deve parlare solo il livelock.
        cfg = _dream_cfg(review_every_runs=99)

        # ``STUCK_FORCES_REVIEW`` run bloccati portano il contatore sulla soglia; il
        # review parte al run successivo, che è quello che la legge.
        for _ in range(STUCK_FORCES_REVIEW + 1):
            await self._cycle(agent, memory, cfg, advanced=False)
        assert len(spy.calls) == 1

        # Il cursore avanza: l'episodio è chiuso e ``stuck`` riparte da zero.
        await self._cycle(agent, memory, cfg, advanced=True)
        assert memory.get_review_state()[1] == 0

        for _ in range(STUCK_FORCES_REVIEW + 1):
            await self._cycle(agent, memory, cfg, advanced=False)
        assert len(spy.calls) == 2

    async def test_the_run_that_advances_does_not_itself_force_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L'azzeramento non riapre la porta a metà episodio: il memo vale finché
        ``stuck`` non si muove, e un review appena forzato non si ripete."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(0, STUCK_FORCES_REVIEW))
        memory.set_review_state(
            runs_since_review=0,
            stuck_runs=STUCK_FORCES_REVIEW,
            forced_at_stuck=STUCK_FORCES_REVIEW,
        )
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)
        agent = _FakeAgent(tmp_path, memory)

        await self._cycle(agent, memory, _dream_cfg(review_every_runs=99), advanced=True)

        assert spy.calls == []
        assert memory.get_review_forced_at_stuck() == 0


class TestAFailedReviewDoesNotBuyAFullCadence:
    """Un review che ha fallito non ha fatto la manutenzione.

    L'azzeramento era incondizionato, quindi anche uno ``STATUS_FAILED`` — che il
    ramo della migrazione troncata usa per dire "del contenuto voluto non è
    atterrato" — si comprava l'intero intervallo, con il default circa un giorno.
    Proprio il caso in cui tornare presto conta di più.
    """

    async def test_a_failed_review_comes_back_in_two_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(12, 0))
        monkeypatch.setattr(_REVIEW_TARGET, _ReviewSpy(memory, status=STATUS_FAILED))

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory), store=memory, cfg=_dream_cfg(review_every_runs=12)
        )

        runs, _stuck = memory.get_review_state()
        assert runs == 12 - REVIEW_RETRY_AFTER_RUNS
        assert prologue.runs_since_review == 0

    async def test_it_is_not_retried_on_every_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La protezione di costo per cui l'azzeramento incondizionato esisteva."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(12, 0))
        spy = _ReviewSpy(memory, status=STATUS_FAILED)
        monkeypatch.setattr(_REVIEW_TARGET, spy)
        agent = _FakeAgent(tmp_path, memory)
        cfg = _dream_cfg(review_every_runs=12)

        await begin_dream_cycle(agent, store=memory, cfg=cfg)
        await begin_dream_cycle(agent, store=memory, cfg=cfg)

        assert len(spy.calls) == 1

    async def test_a_successful_review_still_resets_to_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(12, 0))
        monkeypatch.setattr(_REVIEW_TARGET, _ReviewSpy(memory))

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory), store=memory, cfg=_dream_cfg(review_every_runs=12)
        )

        assert memory.get_review_state() == (0, 0)


class TestSnapshot:
    async def test_no_callback_means_not_snapshotted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``take_snapshot=None`` **è** ``snapshotted=False``, senza che nessuno lo scriva.

        È il punto del parametro: un percorso che non ha modo di prendere il
        checkpoint deve dire la verità al prompt del review, non dimenticare la
        domanda. Il ramo "le tue modifiche sono reversibili" è attaccato alla
        frase il cui unico scopo è far cancellare di più.
        """
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
            take_snapshot=None,
        )

        assert spy.calls[0]["snapshotted"] is False

    async def test_a_checkpoint_that_raises_is_fail_open_and_honest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uno snapshot che solleva non ferma il consolidamento e non mente."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        async def broken() -> bool:
            memory.events.append("snapshot")
            raise RuntimeError("checkpoint guasto")

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
            take_snapshot=broken,
        )

        assert spy.calls[0]["snapshotted"] is False
        assert prologue.review is not None
        assert memory.events[:2] == ["snapshot", "review:snapshotted=False"]

    async def test_it_precedes_the_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        async def checkpoint() -> bool:
            memory.events.append("snapshot")
            return True

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
            take_snapshot=checkpoint,
        )

        assert memory.events[0] == "snapshot"
        assert spy.calls[0]["snapshotted"] is True

    async def test_a_checkpoint_that_reports_false_is_not_upgraded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Con gli snapshot spenti il callback c'è ma non fa nulla, e lo dice."""
        memory = _FakeMemory(tmp_path / "ws", review_state=(4, 0))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        async def disabled() -> bool:
            return False

        await begin_dream_cycle(
            _FakeAgent(tmp_path, memory),
            store=memory,
            cfg=_dream_cfg(review_every_runs=4),
            take_snapshot=disabled,
        )

        assert spy.calls[0]["snapshotted"] is False

    async def test_take_dream_snapshot_alone(self) -> None:
        """L'helper che i chiamanti riusano per il checkpoint pre-turno."""
        assert await take_dream_snapshot(None) is False

        async def ok() -> bool:
            return True

        async def broken() -> bool:
            raise RuntimeError("guasto")

        assert await take_dream_snapshot(ok) is True
        assert await take_dream_snapshot(broken) is False


class TestFinishArithmetic:
    async def test_a_run_that_advances_resets_stuck(self, tmp_path: Path) -> None:
        memory = _FakeMemory(tmp_path / "ws")

        assert finish_dream_cycle(
            memory, advanced=True, runs_since_review=3, stuck=1
        ) == (4, 0)
        assert memory.get_review_state() == (4, 0)

    async def test_a_run_that_does_not_advance_raises_stuck(self, tmp_path: Path) -> None:
        """Non avanzare è la semantica corretta: il fatto non è stato scritto.

        La via d'uscita è forzare il review, non allentare il commit — quindi il
        contatore sale e ``internal_run_should_commit`` resta dov'è.
        """
        memory = _FakeMemory(tmp_path / "ws")

        assert finish_dream_cycle(
            memory, advanced=False, runs_since_review=3, stuck=1, refused=1
        ) == (4, 2)
        assert memory.get_review_state() == (4, 2)

    async def test_the_alarming_threshold_logs_at_error(self, tmp_path: Path) -> None:
        """Sopra la soglia ogni run è un turno LLM che non consolida niente.

        Il contatore ci arriva solo passandoglielo: dal ciclo intero non ci si
        arriva, perché il review azzera ``stuck`` e il valore satura a
        ``STUCK_FORCES_REVIEW`` (v. il commento sulla costante). Qui si verifica
        il contratto della funzione, non una traiettoria che oggi esista.
        """
        memory = _FakeMemory(tmp_path / "ws")

        with _cycle_logs() as messages:
            finish_dream_cycle(
                memory, advanced=False, runs_since_review=9,
                stuck=STUCK_IS_ALARMING - 1, refused=1,
            )

        assert any(m.startswith("ERROR: Dream has not advanced") for m in messages)

    async def test_below_the_threshold_says_nothing(self, tmp_path: Path) -> None:
        memory = _FakeMemory(tmp_path / "ws")

        with _cycle_logs() as messages:
            finish_dream_cycle(
                memory, advanced=False, runs_since_review=1,
                stuck=STUCK_IS_ALARMING - 2, refused=1,
            )

        assert not [m for m in messages if m.startswith("ERROR")]


class TestBudgetLogLine:
    async def test_it_is_emitted_on_every_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Con i budget a 0 questa riga è l'unica cosa che la feature produce.

        Ed è la riga da cui si scelgono i tetti veri, cioè quella che serve a chi
        sta tarando — che è esattamente chi lancia Dream a mano.
        """
        memory = _FakeMemory(tmp_path / "ws", memory_text="x" * 200, review_state=(3, 1))

        with _cycle_logs() as messages:
            await begin_dream_cycle(
                _FakeAgent(tmp_path, memory), store=memory, cfg=_dream_cfg(memory_budget=50)
            )

        # ``USER.md`` porta il proprio tetto di spedizione (3.000) perché
        # ``_dream_cfg`` sovrascrive solo quello di MEMORY.md; SOUL.md resta
        # l'unico dei tre senza enforcement, ed è la controprova che la riga
        # distingue i due stati invece di renderli uguali.
        assert messages == [
            "INFO: Dream memory budget: MEMORY.md 200/50 (400%), USER.md 1/4000 (0%), "
            "SOUL.md 1 (no budget) | runs since review: 3, stuck runs: 1"
        ]

    async def test_an_empty_report_still_renders(self) -> None:
        assert format_budget([]) == "no files"


# ---------------------------------------------------------------------------
# 2. I due chiamanti, sullo stesso stato
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scenario:
    """Uno stato di partenza da far attraversare a entrambi i percorsi."""

    memory_text: str = "m"
    review_state: tuple[int, int] = (0, 0)
    memory_budget: int = 0
    review_every_runs: int = 12
    blocked_writes: bool = False
    with_snapshot: bool = True
    shrink_to: str | None = None
    turn_explodes: bool = False


_SCENARIOS = {
    # Il caso di gran lunga più frequente: niente in scadenza, il cursore avanza.
    "plain": _Scenario(),
    # Cadenza periodica scaduta: parte il review, che pota davvero — così le
    # misure ricostruite dopo sono diverse da quelle di prima e un percorso che
    # riusasse le vecchie si vedrebbe.
    "cadence": _Scenario(
        memory_text="x" * 200,
        review_state=(4, 0),
        memory_budget=50,
        review_every_runs=4,
        shrink_to="x" * 20,
    ),
    # Livelock: il budget rifiuta le scritture, ``stuck`` ha già forzato il
    # review, e non c'è nessun servizio di snapshot da cui prendere il checkpoint.
    "stuck_without_snapshot": _Scenario(
        memory_text="x" * 200,
        review_state=(0, STUCK_FORCES_REVIEW),
        memory_budget=50,
        review_every_runs=99,
        blocked_writes=True,
        with_snapshot=False,
    ),
    # Il turno incrementale solleva. Prima della correzione del 2026-08-17 i
    # contatori non venivano scritti affatto — ``finish_dream_cycle`` era l'ultima
    # istruzione del ``try`` — quindi ``runs_since_review`` non avanzava e un Dream
    # che crasha a ogni run non arrivava **mai** a un review pass. Entrambi i
    # percorsi avevano la stessa forma, quindi la traccia restava uguale: è per
    # questo che serve uno scenario, e non basta il confronto fra i due.
    "crashing_turn": _Scenario(turn_explodes=True),
}


def _install_scenario(
    scenario: _Scenario, memory: _FakeMemory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cabla review spy e knob per un percorso.

    I knob passano da ``load_config``, non da un ``Config`` passato a mano: è
    così che li leggono **entrambi** i percorsi, a ogni run e da disco, perché il
    ``Config`` catturato dal container non viene aggiornato da nessuno quando
    ``/dream budget`` riscrive il file. Sostituire la lettura invece di
    scavalcarla è ciò che tiene il test aderente al comportamento vero.
    """
    monkeypatch.setattr(_REVIEW_TARGET, _ReviewSpy(memory, shrink_to=scenario.shrink_to))
    config = _config(
        memory_budget=scenario.memory_budget,
        review_every_runs=scenario.review_every_runs,
    )
    monkeypatch.setattr("jenny.config.loader.load_config", lambda *a, **k: config)


def _build(scenario: _Scenario, root: Path) -> tuple[_FakeMemory, _FakeAgent]:
    memory = _FakeMemory(
        root / "ws",
        memory_text=scenario.memory_text,
        review_state=scenario.review_state,
        file_states=(
            _blocked_writes()
            if scenario.blocked_writes
            else _successful_writes(root / "written.md")
        ),
    )

    async def checkpoint() -> bool:
        memory.events.append("snapshot")
        return True

    agent = _FakeAgent(
        root,
        memory,
        snapshot_before_dream=checkpoint if scenario.with_snapshot else None,
        turn_explodes=scenario.turn_explodes,
    )
    return memory, agent


async def _run_cron(
    scenario: _Scenario, root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], list[str]]:
    memory, agent = _build(scenario, root)
    _install_scenario(scenario, memory, monkeypatch)
    dispatcher = CronDispatcher(
        get_agent=lambda: agent,
        config=Config(),
        cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(),
        snapshot_before_dream=agent.snapshot_before_dream,
    )

    with _cycle_logs() as messages:
        await dispatcher.dispatch(_DREAM_JOB)
    return memory.events, messages


async def _run_manual(
    scenario: _Scenario, root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], list[str]]:
    memory, loop = _build(scenario, root)
    _install_scenario(scenario, memory, monkeypatch)
    router = CommandRouter()
    register_builtin_commands(router)
    msg = InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content="/dream"
    )
    ctx = CommandContext(
        msg=msg, session=None, key="k", raw="/dream", args="", loop=loop
    )

    with _cycle_logs() as messages:
        await router.dispatch(ctx)
        deadline = time.monotonic() + 5.0
        while not loop.published and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        assert loop.published, "`/dream` non ha pubblicato nessuna risposta"
    return memory.events, messages


class TestTheTwoCallersStayAligned:
    """Il test per cui l'estrazione esiste.

    Non confronta il codice dei due percorsi — confronta ciò che se ne vede da
    fuori: la sequenza esatta delle chiamate sullo store (checkpoint, review,
    scritture dei contatori, gauge nel prompt, guard montato sui tool, cursore) e
    le righe che il ciclo emette nei log. Sono le sette divergenze del catalogo,
    espresse in una forma che ne prende anche l'ottava: qualunque cosa uno dei
    due chiamanti smetta di fare, o cominci a fare in un altro momento, rompe
    l'uguaglianza.

    Ciò che resta legittimamente diverso — la risposta in chat contro la riga di
    log, il tempo trascorso, la sorgente del fuso per i token — non passa da qui
    per costruzione: non tocca lo store e non lo scrive ``dream_cycle``.
    """

    @pytest.mark.parametrize("name", sorted(_SCENARIOS))
    async def test_same_observable_trace(
        self, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = _SCENARIOS[name]

        with monkeypatch.context() as m:
            cron_events, cron_logs = await _run_cron(scenario, tmp_path / "cron", m)
        with monkeypatch.context() as m:
            manual_events, manual_logs = await _run_manual(
                scenario, tmp_path / "manual", m
            )

        assert cron_events == manual_events
        assert cron_logs == manual_logs

    @pytest.mark.parametrize("run", [_run_cron, _run_manual])
    async def test_a_crashing_turn_still_closes_the_cycle(
        self, run: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il caso che l'uguaglianza fra i due percorsi non può prendere.

        Prima della correzione del 2026-08-17 ``finish_dream_cycle`` era l'ultima
        istruzione del ``try``, quindi un turno che solleva la saltava del tutto:
        ``runs_since_review`` non avanzava e un Dream che crasha a ogni run non
        arrivava **mai** a un review pass — invisibile, perché il crash era già
        loggato e sembrava l'unico problema. Entrambi i percorsi avevano la stessa
        forma, quindi il confronto fra loro restava verde: serve un'asserzione su
        cosa la traccia deve contenere.

        ``stuck`` non si muove, di proposito: il suo allarme dice "le scritture
        continuano a essere rifiutate dal budget", che di un'eccezione è una
        diagnosi sbagliata — e finisce su una notifica che l'utente legge.
        """
        with monkeypatch.context() as m:
            events, _logs = await run(_SCENARIOS["crashing_turn"], tmp_path, m)

        assert "process_direct" in events, "il turno non è nemmeno partito"
        assert events[-1] == "review_state:1,0"

    async def test_the_trace_is_not_vacuously_equal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La rete sotto la rete: il confronto deve poter fallire.

        Un test di uguaglianza fra due tracce vuote passerebbe per sempre e non
        direbbe niente. Qui si fissa cosa deve contenere la traccia dello
        scenario più ricco — checkpoint, review, azzeramento, gauge misurato
        dopo il review, guard che rifiuta, e i contatori riscritti alla fine.
        """
        with monkeypatch.context() as m:
            events, logs = await _run_cron(_SCENARIOS["cadence"], tmp_path, m)

        assert events == [
            "snapshot",
            "review:snapshotted=True",
            "review_state:0,0",
            "prompt:MEMORY.md [40% — 20/50 chars]",
            "tools:refuses=True",
            "process_direct",
            "cursor:42",
            "review_state:1,0",
        ]
        assert [m.split(":")[0] for m in logs] == ["INFO", "INFO"]


# ---------------------------------------------------------------------------
# 3. Il checkpoint sul percorso manuale
# ---------------------------------------------------------------------------


class TestTheManualRunTakesTheSnapshot:
    """Punto 5 del catalogo: `/dream` a mano non prendeva **nessuno** checkpoint.

    Era già un buco quando quel comando faceva solo consolidamento incrementale.
    Da ``04de3cc`` può far partire un review pass — un turno esplicitamente
    autorizzato a ristrutturare e cancellare — e girarlo senza rete è un'altra
    cosa. Il servizio snapshot ce l'ha il container, che ora lo appende al loop.
    """

    async def test_the_incremental_turn_is_checkpointed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events, _ = await _run_manual(_SCENARIOS["plain"], tmp_path, monkeypatch)

        assert "snapshot" in events
        assert events.index("snapshot") < events.index("process_direct")

    async def test_a_loop_without_the_service_reports_no_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Senza il servizio il run prosegue, e il review pass lo sa.

        ``getattr(loop, "snapshot_before_dream", None)`` copre anche il loop che
        l'attributo non ce l'ha affatto — un test, un percorso che costruisce
        l'agente da sé — e la traduzione è la stessa: nel dubbio si mente al
        ribasso, mai al rialzo.
        """
        scenario = _SCENARIOS["stuck_without_snapshot"]
        events, _ = await _run_manual(scenario, tmp_path, monkeypatch)

        assert "snapshot" not in events
        assert "review:snapshotted=False" in events

    async def test_only_one_checkpoint_per_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Se il review è girato, il suo snapshot copre anche il turno che segue.

        Un secondo "pre_dream" archiviato dopo il review non sarebbe pre-niente.
        """
        events, _ = await _run_manual(_SCENARIOS["cadence"], tmp_path, monkeypatch)

        assert events.count("snapshot") == 1


class TestTheAlarmCanActuallySound:
    """``STUCK_IS_ALARMING`` era codice morto, ed era l'unico allarme che c'e'.

    Il review azzerava ``stuck``, quindi il contatore oscillava 1,2,1,2 e la
    soglia a 4 non si raggiungeva mai: un Dream livelockato in modo permanente
    non avrebbe prodotto una sola riga di ERROR, cioe' esattamente il "un
    controllo rotto resta rotto in silenzio" che questo ramo esiste per chiudere.

    La correzione e' di semantica, non di soglia: ``stuck`` conta i run
    consecutivi in cui Dream non consolida, e a quella domanda un review appena
    girato non e' una risposta. Lo azzera solo un cursore che avanza.
    """

    async def test_a_review_does_not_clear_the_stuck_counter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(0, 2))
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)

        prologue = await begin_dream_cycle(
            _FakeAgent(tmp_path, memory), store=memory, cfg=_dream_cfg()
        )

        assert spy.ran, "due run bloccati devono forzare il review"
        # La cadenza periodica riparte; il conteggio dei run bloccati no.
        assert prologue.runs_since_review == 0
        assert prologue.stuck == 2
        assert memory.get_review_state() == (0, 2)

    async def test_a_persistent_livelock_reaches_the_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il caso vero, simulato per intero: nessun run avanza mai.

        Prima della correzione questa sequenza produceva ``stuck`` massimo 2 e
        nessun ERROR, per sempre.
        """
        memory = _FakeMemory(tmp_path / "ws")
        spy = _ReviewSpy(memory)
        monkeypatch.setattr(_REVIEW_TARGET, spy)
        agent = _FakeAgent(tmp_path, memory)

        seen: list[int] = []
        reviews = 0
        with _cycle_logs() as logs:
            for _ in range(6):
                runs, stuck = memory.get_review_state()
                prologue = await begin_dream_cycle(
                    agent, store=memory, cfg=_dream_cfg()
                )
                reviews += prologue.review is not None
                _, stuck = finish_dream_cycle(
                    memory,
                    advanced=False,
                    refused=1,
                    runs_since_review=prologue.runs_since_review + 1,
                    stuck=prologue.stuck,
                )
                seen.append(stuck)

            assert seen == [1, 2, 3, 4, 5, 6], seen
            # Cadenza invariata rispetto a prima: il review parte quando il
            # contatore *entra* nel run valendo un multiplo di due, cioe' al
            # terzo e al quinto di questi sei. Uno ogni due run bloccati, che e'
            # esattamente quello che faceva l'azzeramento — la correzione tocca
            # cosa il contatore significa, non quanto spesso si spende.
            assert reviews == 2, reviews
            assert any("no longer consolidating" in line or "ERROR" in line
                       for line in logs), logs


class TestTheAlarmLeavesTheLog:
    """Su Android il ``logger.error`` è un allarme che non suona.

    Nessuno legge logcat sul telefono, e la sola altra superficie — le misure
    in Impostazioni → Memoria — risponde a chi è già andato a guardare. Un Dream fermo
    per giorni resterebbe quindi invisibile esattamente come prima della
    correzione che ha reso raggiungibile la soglia: la si raggiunge, e non lo
    sa nessuno.

    ``notify_delivery`` è la primitiva che il canale WS usa già per gli alert di
    consegna: fire-and-forget, zero token, no-op fuori da Android. Non il tool
    ``message`` dell'heartbeat, che costa un turno LLM e dipende dal modello che
    sceglie di chiamarlo — e il modello, qui, è la parte che non funziona.
    """

    @staticmethod
    def _spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
        sent: list[tuple[str, Any]] = []
        monkeypatch.setattr(
            "jenny.runtime.notifier.notify_delivery",
            lambda content, metadata: sent.append((content, metadata)),
        )
        return sent

    async def test_crossing_the_threshold_posts_an_alert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = self._spy(monkeypatch)
        memory = _FakeMemory(tmp_path / "ws")

        finish_dream_cycle(
            memory, advanced=False, runs_since_review=9,
            stuck=STUCK_IS_ALARMING - 1, refused=1,
        )

        assert len(sent) == 1
        content, metadata = sent[0]
        assert format_stuck_alarm(STUCK_IS_ALARMING) in content
        # L'alert dice dove andare a vedere i numeri, che qui non ci sono.
        # Era ``/dream budget``, rimosso il 31/08/2026: la superficie ora è la
        # sezione Memoria delle Impostazioni, e l'alert deve nominare *quella*
        # — mandare a un comando che non esiste è peggio che non dire niente.
        assert "Settings \u2192 Memory" in content
        assert metadata == {
            WEBUI_MESSAGE_SOURCE_METADATA_KEY: {"kind": "cron", "label": "Dream"}
        }

    async def test_below_the_threshold_posts_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = self._spy(monkeypatch)
        memory = _FakeMemory(tmp_path / "ws")

        finish_dream_cycle(
            memory, advanced=False, runs_since_review=1,
            stuck=STUCK_IS_ALARMING - 2, refused=1,
        )

        assert sent == []

    async def test_it_rearms_every_run_and_never_stacks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Riparte a ogni run oltre soglia, ma il tag lo fa sostituire.

        Chi ha scartato la notifica la rivede al giro dopo — voluto, per un
        allarme che significa "la memoria è ferma" — e sul telefono ne resta
        comunque una sola, sempre col conteggio aggiornato. Il tag è suo: con
        quello di default (``message``) andrebbe a coprire la notifica di un
        messaggio vero.
        """
        sent = self._spy(monkeypatch)
        memory = _FakeMemory(tmp_path / "ws", review_state=(0, STUCK_IS_ALARMING))

        for _ in range(3):
            runs, stuck = memory.get_review_state()
            finish_dream_cycle(
                memory, advanced=False, runs_since_review=runs, stuck=stuck, refused=1,
            )

        tags = {alert_fields(content, metadata)[2] for content, metadata in sent}
        assert len(sent) == 3
        assert tags == {"cron:Dream"}
        assert tags != {alert_fields("qualsiasi", None)[2]}
        # Il conteggio nel corpo cresce: l'alert che sostituisce il precedente
        # non è una copia, è la misura aggiornata.
        assert [alert_fields(c, m)[1] for c, m in sent] == [
            f"{format_stuck_alarm(n)} Settings \u2192 Memory shows the sizes."
            for n in (STUCK_IS_ALARMING + 1, STUCK_IS_ALARMING + 2, STUCK_IS_ALARMING + 3)
        ]

    async def test_the_body_survives_the_notification_size_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Il notifier tronca a 200 caratteri, e un allarme mozzato perde la parte utile.

        La coda della frase è l'unico pezzo azionabile che contiene — il comando
        da lanciare — quindi è precisamente quella che una troncatura mangia.
        """
        sent = self._spy(monkeypatch)
        memory = _FakeMemory(tmp_path / "ws")

        finish_dream_cycle(memory, advanced=False, runs_since_review=0, stuck=9998)

        _, body, _ = alert_fields(*sent[0])
        assert not body.endswith("…"), body
        assert body.endswith("Settings \u2192 Memory shows the sizes.")


class TestACycleWithNothingToConsolidate:
    """Un run senza storia da processare non e' un run fallito, ma e' un run.

    Prima, il ramo "nothing to process" usciva prima di ``finish_dream_cycle``,
    quindi ``runs_since_review`` non avanzava. Su un'installazione in pari con
    la storia — Dream ha digerito tutto, i file stanno fermi — il contatore
    restava a zero e il review pass non partiva **mai**, da nessuno dei due
    percorsi. Il review e' manutenzione sui file: legarlo all'arrivo di nuova
    storia lega due cose scorrelate, e le lega male proprio nel caso in cui la
    manutenzione avrebbe piu' senso.

    Misurato sul Titan 2 il 2026-08-16: cursore a 88, ``history.jsonl`` a 23
    voci, `.dream_review` inesistente dopo un `/dream` andato a buon fine.
    """

    async def test_the_cadence_advances_without_history(self, tmp_path: Path) -> None:
        memory = _FakeMemory(tmp_path / "ws", review_state=(3, 1))

        runs, stuck = finish_dream_cycle(
            memory, advanced=None, runs_since_review=3, stuck=1
        )

        assert (runs, stuck) == (4, 1)
        assert memory.get_review_state() == (4, 1)

    async def test_it_does_not_count_as_a_failure_to_advance(
        self, tmp_path: Path
    ) -> None:
        """``stuck`` conta i run in cui Dream *non e' riuscito* a consolidare.

        Un run che non aveva niente da fare non ha fallito niente: contarlo
        farebbe scattare il review — e a soglia 4 l'allarme — su
        un'installazione perfettamente sana che semplicemente non chatta.
        """
        memory = _FakeMemory(tmp_path / "ws", review_state=(0, 0))

        for _ in range(6):
            runs, stuck = memory.get_review_state()
            finish_dream_cycle(
                memory, advanced=None, runs_since_review=runs, stuck=stuck
            )

        runs, stuck = memory.get_review_state()
        assert stuck == 0, "sei run a vuoto non sono sei fallimenti"
        assert runs == 6, "ma la cadenza del review e' avanzata di sei"
