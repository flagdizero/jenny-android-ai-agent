"""Il job Dream prende la stessa presa di `/dream`.

I job cron sono serializzati fra loro, quindi il caso raggiungibile è l'incrocio
fra i due percorsi — `/dream` a mano mentre il job delle due ore gira. Ma la
presa va provata **da qui** e non solo dal comando: la ragione per cui il prologo
e l'epilogo del ciclo vivono in un modulo condiviso è che ogni guardia cablata su
un percorso solo è arrivata all'altro tre commit dopo, in silenzio.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from support.aio import wait_until

from jenny.agent import dream_cycle
from jenny.agent.tools.file_state import FileStates
from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_DREAM_JOB = SimpleNamespace(name="dream", id="job-dream")


@pytest.fixture(autouse=True)
def _clean_guard():
    dream_cycle.release_dream_cycle()
    yield
    dream_cycle.release_dream_cycle()


class _FakeMemory:
    memory_file = Path("no-such-MEMORY.md")
    user_file = Path("no-such-USER.md")
    soul_file = Path("no-such-SOUL.md")

    def __init__(self) -> None:
        self.cursor: int | None = None
        self.tools = SimpleNamespace(file_states=FileStates())
        # I contatori sono veri (in memoria): è il read-modify-write che due
        # cicli sovrapposti fanno perdere un tick.
        self.runs_since_review = 0
        self.stuck = 0
        self.written: list[dict] = []

    def get_review_state(self) -> tuple[int, int]:
        return (self.runs_since_review, self.stuck)

    def get_nothing_new_runs(self) -> int:
        return 0

    def get_review_forced_at_stuck(self) -> int:
        return 0

    def set_review_state(self, **kwargs) -> None:
        self.written.append(kwargs)
        if "runs_since_review" in kwargs:
            self.runs_since_review = kwargs["runs_since_review"]
        if "stuck_runs" in kwargs:
            self.stuck = kwargs["stuck_runs"]

    def build_dream_prompt(self, **_kwargs):
        return ("prompt di consolidamento", 42)

    def build_dream_tools(self, **_kwargs):
        return self.tools

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.cursor = cursor

    def get_last_dream_cursor(self) -> int:
        return 7

    def compact_history(self) -> None:
        pass


class _GatedAgent:
    """Agente il cui turno resta fermo finché il gate non si apre."""

    def __init__(self, sessions_dir: Path, memory: _FakeMemory) -> None:
        self.context = SimpleNamespace(memory=memory)
        self.sessions = SimpleNamespace(sessions_dir=sessions_dir)
        self.gate: asyncio.Event | None = None
        self.turns = 0

    async def process_direct(self, prompt: str, **_kwargs):
        self.turns += 1
        if self.gate is not None:
            await self.gate.wait()
        return SimpleNamespace(metadata={"_stop_reason": "completed"}, usage={})

    def evict_pruned_sessions(self, keys) -> None:
        pass


def _dispatcher(agent: _GatedAgent) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: agent,
        config=Config(),
        cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(),
    )


async def _wait_for_the_turn(agent: _GatedAgent) -> None:
    """Attende che il primo ciclo sia davvero dentro il turno."""
    await wait_until(
        lambda: agent.turns,
        timeout=2.5,
        interval=0.005,
        msg="il primo ciclo Dream non è mai arrivato al turno",
    )


async def _second_tick(dispatcher: CronDispatcher, *, timeout: float = 2.0):
    """Il secondo tick deve **tornare**, non mettersi in coda.

    Un ``wait_for`` e non un ``await`` nudo: senza la presa il secondo ciclo
    entra nel turno e si blocca sul gate, e il test si impianterebbe invece di
    fallire — che è il modo peggiore di segnalare un difetto.
    """
    try:
        return await asyncio.wait_for(dispatcher.dispatch(_DREAM_JOB), timeout)
    except asyncio.TimeoutError:
        pytest.fail("il secondo tick di Dream non è stato rifiutato: si è messo in coda")


# ---------------------------------------------------------------------------
# (b) Due dispatch del job non possono sovrapporsi
# ---------------------------------------------------------------------------


async def test_two_cron_dispatches_cannot_overlap(tmp_path: Path) -> None:
    memory = _FakeMemory()
    agent = _GatedAgent(tmp_path, memory)
    agent.gate = asyncio.Event()
    dispatcher = _dispatcher(agent)

    first = asyncio.create_task(dispatcher.dispatch(_DREAM_JOB))
    await _wait_for_the_turn(agent)
    assert agent.turns == 1

    second = await _second_tick(dispatcher)
    assert second == "dream: already running"
    assert agent.turns == 1

    agent.gate.set()
    assert await first is None
    assert agent.turns == 1


async def test_the_refused_tick_leaves_the_counters_alone(tmp_path: Path) -> None:
    """Un solo tick per ciclo: due cicli sovrapposti ne perderebbero uno."""
    memory = _FakeMemory()
    memory.runs_since_review = 3
    agent = _GatedAgent(tmp_path, memory)
    agent.gate = asyncio.Event()
    dispatcher = _dispatcher(agent)

    first = asyncio.create_task(dispatcher.dispatch(_DREAM_JOB))
    await _wait_for_the_turn(agent)

    await _second_tick(dispatcher)
    agent.gate.set()
    await first

    # Le **scritture** sono una. Il valore finale da solo mentirebbe: due cicli
    # sovrapposti leggono entrambi 3 e scrivono entrambi 4.
    assert len(memory.written) == 1
    assert memory.get_review_state() == (4, 0)


async def test_a_second_tick_after_the_first_finished_runs(tmp_path: Path) -> None:
    """Controprova: la presa non è un interruttore che resta giù."""
    memory = _FakeMemory()
    agent = _GatedAgent(tmp_path, memory)
    dispatcher = _dispatcher(agent)

    assert await dispatcher.dispatch(_DREAM_JOB) is None
    assert await dispatcher.dispatch(_DREAM_JOB) is None
    assert agent.turns == 2


# ---------------------------------------------------------------------------
# (d) La presa si rende anche quando il ciclo solleva
# ---------------------------------------------------------------------------


async def test_a_raising_cycle_releases_the_guard(tmp_path: Path) -> None:
    """``prune_dream_sessions`` sta fuori dal ``try`` del ciclo: se solleva, esce.

    Senza il ``try``/``finally`` un livello più in fuori, il job Dream non
    ripartirebbe più fino al riavvio del gateway — e niente lo direbbe.
    """
    memory = _FakeMemory()
    agent = _GatedAgent(tmp_path, memory)
    dispatcher = _dispatcher(agent)

    # ``sessions_dir`` non è un path: ``prune_dream_sessions`` esplode.
    agent.sessions = SimpleNamespace(sessions_dir=None)

    with pytest.raises(Exception):
        await dispatcher.dispatch(_DREAM_JOB)

    assert not dream_cycle._CYCLE_IN_FLIGHT
    # E la prova osservabile: il tick dopo parte.
    agent.sessions = SimpleNamespace(sessions_dir=tmp_path)
    assert await dispatcher.dispatch(_DREAM_JOB) is None
    assert agent.turns == 2


async def test_a_cancelled_cycle_releases_the_guard(tmp_path: Path) -> None:
    memory = _FakeMemory()
    agent = _GatedAgent(tmp_path, memory)
    agent.gate = asyncio.Event()
    dispatcher = _dispatcher(agent)

    task = asyncio.create_task(dispatcher.dispatch(_DREAM_JOB))
    await _wait_for_the_turn(agent)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not dream_cycle._CYCLE_IN_FLIGHT
