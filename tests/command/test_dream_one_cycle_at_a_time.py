"""Un ciclo di Dream per volta, dal lato di `/dream`.

`cmd_dream` faceva ``asyncio.create_task`` senza chiedere niente a nessuno, e il
lock per sessione non separa i due percorsi perché ``dream_session_key()`` è una
chiave nuova a ogni run. Il caso raggiungibile è `/dream` battuto mentre il job
delle due ore gira, oppure `/dream` due volte: entrambi leggono
``runs_since_review``, e se il review è dovuto lo eseguono **tutti e due**.

Il costo di due passate di review consecutive è token e rumore, non fatti persi:
da T2.4b ``make_entry_archiver`` sta al confine del file su tutti i tool di Dream.
È il motivo per cui il rifiuto qui sotto dice "spent twice" e non "lost".
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent import dream_cycle
from jenny.agent.memory import MemoryStore
from jenny.bus.events import InboundMessage
from jenny.command.builtin import register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.config.loader import save_config
from jenny.config.schema import Config
from jenny.utils.helpers import sync_workspace_templates

_REVIEW_TARGET = "jenny.agent.dream_review.run_dream_review"
_MEMORY_TEXT = "# Memory\n- seed fact\n"


@pytest.fixture(autouse=True)
def _clean_guard():
    """La presa è di processo: un test che la lascia presa ne romperebbe gli altri."""
    dream_cycle.release_dream_cycle()
    yield
    dream_cycle.release_dream_cycle()


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from jenny.runtime.context import get_runtime_context
    from jenny.utils import prompt_templates

    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    sync_workspace_templates(ws, silent=True)

    ctx = get_runtime_context()
    monkeypatch.setattr(ctx, "workspace_dir", ws)
    monkeypatch.setattr(ctx, "config_path", ws / "config.json")
    prompt_templates._environment.cache_clear()
    save_config(Config(), ws / "config.json")
    yield ws
    prompt_templates._environment.cache_clear()


@pytest.fixture()
def memory(workspace: Path) -> MemoryStore:
    store = MemoryStore(workspace)
    store.memory_file.write_text(_MEMORY_TEXT, encoding="utf-8")
    store.user_file.write_text("# User\n", encoding="utf-8")
    store.soul_file.write_text("# Soul\n", encoding="utf-8")
    store.append_history("user: ricordati che il gateway gira su Android")
    return store


class _GatedLoop:
    """``AgentLoop`` minimale il cui turno si può tenere fermo a comando.

    ``gate`` è ciò che rende osservabile la sovrapposizione: finché è chiuso il
    primo ciclo è in volo, e un secondo `/dream` incontra la presa presa.
    """

    def __init__(self, workspace: Path, memory: MemoryStore) -> None:
        self.bus = SimpleNamespace(publish_outbound=self._publish)
        self.context = SimpleNamespace(memory=memory, timezone=None)
        self.sessions = SimpleNamespace(sessions_dir=workspace / "sessions")
        self.published: list[Any] = []
        self.prompts: list[str] = []
        self.gate: asyncio.Event | None = None
        self._background_tasks: list[asyncio.Task] = []

    async def _publish(self, message: Any) -> None:
        self.published.append(message)

    async def process_direct(self, prompt: str, **_kwargs: Any):
        self.prompts.append(prompt)
        if self.gate is not None:
            await self.gate.wait()
        return SimpleNamespace(content="done", metadata={"_stop_reason": "completed"})

    def _schedule_background(self, coro: Any) -> None:
        # Come ``AgentLoop._schedule_background``: il task nasce da
        # ``asyncio.create_task``, cosi' i test che lo sostituiscono vedono
        # ancora la stessa chiamata. E come quello raccoglie l'eccezione di un
        # task fallito: senza, un ciclo che solleva («disk on fire») sporcava
        # l'uscita della suite con «Task exception was never retrieved».
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())

    def evict_pruned_sessions(self, _keys: Any) -> None:
        pass


@pytest.fixture()
def loop(workspace: Path, memory: MemoryStore) -> _GatedLoop:
    return _GatedLoop(workspace, memory)


def _ctx(loop: _GatedLoop, raw: str = "/dream") -> CommandContext:
    args = raw[len("/dream"):].strip()
    msg = InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content=raw
    )
    return CommandContext(msg=msg, session=None, key="k", raw=raw, args=args, loop=loop)


async def _drain(loop: _GatedLoop, *, expected: int = 1, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while len(loop.published) < expected and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    assert len(loop.published) >= expected, "`/dream` non ha pubblicato la risposta"


async def _wait_guard_free(timeout: float = 5.0) -> None:
    """Attende che la presa torni libera.

    Legge il registro privato di proposito: il rilascio è ciò che il test misura,
    e aspettare un effetto collaterale osservabile (la risposta) non basta per un
    ciclo che muore prima di pubblicarla.
    """
    deadline = time.monotonic() + timeout
    while dream_cycle._CYCLE_IN_FLIGHT and time.monotonic() < deadline:
        await asyncio.sleep(0.005)


class _ReviewSpy:
    """Sostituto di ``run_dream_review``, tenibile fermo a comando.

    Il gate sta **dentro** il review e non nel turno incrementale, e la
    differenza è tutto il test: ``begin_dream_cycle`` legge ``runs_since_review``
    e lo riazzera senza mai sospendersi, quindi un gate più a valle lascerebbe il
    primo ciclo arrivare a scrivere lo stato prima che il secondo lo legga — e il
    difetto (due review consecutive) non si vedrebbe nemmeno senza la presa.
    """

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.calls = 0
        self.gate = gate

    async def __call__(
        self, agent, *, store, report, snapshotted, write_size_guard=None, reporter=None
    ):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        before = {item.label: item.chars for item in report}
        return SimpleNamespace(
            status="completed", before=before, after=before, freed=0, demoted_ids=(),
            unresolved_refusals=0,
        )


# ---------------------------------------------------------------------------
# (a) Il secondo `/dream` è rifiutato, e c'è un solo ciclo
# ---------------------------------------------------------------------------


class TestASecondDreamIsRefused:
    @pytest.mark.asyncio
    async def test_the_reply_says_already_running(self, router, loop, memory):
        loop.gate = asyncio.Event()

        first = await router.dispatch(_ctx(loop))
        second = await router.dispatch(_ctx(loop))

        assert first.content == "Dreaming..."
        assert "A Dream cycle is already running" in second.content
        # E dice il costo vero: token spesi due volte, non fatti persi.
        assert "spent twice" in second.content
        assert "lost" not in second.content.replace("Nothing would be lost", "")

        loop.gate.set()
        await _drain(loop)

    @pytest.mark.asyncio
    async def test_only_one_turn_ran(self, router, loop, memory):
        """La prova che il rifiuto è un rifiuto e non solo una frase."""
        loop.gate = asyncio.Event()

        await router.dispatch(_ctx(loop))
        await router.dispatch(_ctx(loop))
        # Il ciclo rifiutato non ha nemmeno un task: dare tempo all'event loop
        # non lo fa comparire.
        await asyncio.sleep(0.02)
        loop.gate.set()
        await _drain(loop)

        assert len(loop.prompts) == 1
        assert len(loop.published) == 1

    @pytest.mark.asyncio
    async def test_the_refusal_is_immediate_and_needs_no_scheduling(
        self, router, loop, memory
    ):
        """La presa si prende prima di ``create_task``.

        Fra i due dispatch non c'è nessun ``await`` che lasci partire il primo
        task: se il controllo stesse dentro il corpo del task, il secondo
        `/dream` lo troverebbe ancora libero e passerebbero entrambi.
        """
        loop.gate = asyncio.Event()

        await router.dispatch(_ctx(loop))
        second = await router.dispatch(_ctx(loop))

        assert loop.prompts == []  # il primo task non è ancora partito
        assert "already running" in second.content

        loop.gate.set()
        await _drain(loop)

    @pytest.mark.asyncio
    async def test_after_the_cycle_ends_dream_is_available_again(
        self, router, loop, memory
    ):
        await router.dispatch(_ctx(loop))
        await _drain(loop)
        await _wait_guard_free()

        again = await router.dispatch(_ctx(loop))
        assert again.content == "Dreaming..."
        await _drain(loop, expected=2)


# ---------------------------------------------------------------------------
# (c) I contatori avanzano una volta per ciclo, e il review gira una volta
# ---------------------------------------------------------------------------


class TestTheCountersAdvanceOncePerCycle:
    @pytest.mark.asyncio
    async def test_a_refused_dream_does_not_touch_the_counters(
        self, router, loop, memory, monkeypatch
    ):
        """Un ciclo, un tick — contati, non dedotti.

        Il valore finale da solo non basterebbe: due cicli sovrapposti leggono
        entrambi 3 e scrivono entrambi 4, quindi lo stato *sembra* giusto e il
        tick perso è invisibile. Sono le **scritture** a doverne essere una.
        """
        loop.gate = asyncio.Event()
        memory.set_review_state(runs_since_review=3, stuck_runs=0)

        ticks: list[dict] = []
        real_set = memory.set_review_state

        def _record(**kwargs) -> None:
            ticks.append(kwargs)
            real_set(**kwargs)

        monkeypatch.setattr(memory, "set_review_state", _record)

        await router.dispatch(_ctx(loop))
        await router.dispatch(_ctx(loop))
        await asyncio.sleep(0.02)
        loop.gate.set()
        await _drain(loop)

        assert len(ticks) == 1
        assert memory.get_review_state() == (4, 0)
        assert len(loop.prompts) == 1

    @pytest.mark.asyncio
    async def test_the_review_pass_runs_once_not_twice(
        self, router, loop, memory, monkeypatch
    ):
        """Il difetto vero: due cicli dovuti al review lo eseguono entrambi."""
        gate = asyncio.Event()
        spy = _ReviewSpy(gate)
        monkeypatch.setattr(_REVIEW_TARGET, spy)
        memory.set_review_state(runs_since_review=12, stuck_runs=0)

        await router.dispatch(_ctx(loop))
        await router.dispatch(_ctx(loop))
        # Il primo ciclo è dentro il review, con ``runs_since_review`` ancora a
        # 12 su disco: è il momento in cui un secondo ciclo lo rileggerebbe.
        await asyncio.sleep(0.02)
        assert spy.calls == 1

        gate.set()
        await _drain(loop)
        assert spy.calls == 1


# ---------------------------------------------------------------------------
# (d) Una presa che resta presa è peggio di nessuna presa
# ---------------------------------------------------------------------------


class TestTheGuardIsAlwaysReleased:
    @pytest.mark.asyncio
    async def test_a_cycle_that_raises_still_releases_it(
        self, router, loop, memory, monkeypatch
    ):
        """``compact_history`` sta nel ``finally`` del ciclo: se solleva, esce.

        È il cammino che il ``try``/``finally`` un livello più in fuori esiste per
        coprire. Senza, Dream resta spento fino al riavvio del processo.
        """

        def _boom() -> None:
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(memory, "compact_history", _boom)

        await router.dispatch(_ctx(loop))
        await _wait_guard_free()

        assert not dream_cycle._CYCLE_IN_FLIGHT
        # E la prova osservabile: un nuovo `/dream` riparte.
        again = await router.dispatch(_ctx(loop))
        assert again.content == "Dreaming..."

    @pytest.mark.asyncio
    async def test_a_task_that_never_starts_releases_it(
        self, router, loop, memory, monkeypatch
    ):
        """Se il task non arriva a esistere, il suo ``finally`` non girerà mai.

        La presa è già stata presa a quel punto — di proposito, prima di
        ``create_task`` — quindi va restituita sul posto.
        """
        def _no_task(coro, *_args, **_kwargs):
            coro.close()  # zittisce il warning: la coroutine non girerà
            raise RuntimeError("event loop is closed")

        monkeypatch.setattr(asyncio, "create_task", _no_task)

        with pytest.raises(RuntimeError):
            await router.dispatch(_ctx(loop))

        assert not dream_cycle._CYCLE_IN_FLIGHT

    @pytest.mark.asyncio
    async def test_a_cycle_cancelled_mid_turn_releases_it(
        self, router, loop, memory
    ):
        """La cancellazione passa dal ``finally`` come ogni altra uscita."""
        loop.gate = asyncio.Event()
        await router.dispatch(_ctx(loop))
        # Aspetta che il turno sia davvero dentro il gate, poi cancellalo.
        deadline = time.monotonic() + 5.0
        while not loop.prompts and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        assert loop.prompts

        for task in asyncio.all_tasks():
            if task.get_coro().__qualname__.endswith("cmd_dream.<locals>._run_dream"):
                task.cancel()
                break
        else:  # pragma: no cover — il task esiste, il turno è in volo
            pytest.fail("il task di `/dream` non è stato trovato")

        await _wait_guard_free()
        assert not dream_cycle._CYCLE_IN_FLIGHT

