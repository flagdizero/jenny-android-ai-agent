"""Test del wiring snapshot nel ``GatewayContainer``.

Verifica i contratti di integrazione senza costruire il grafo completo:
il checkpoint pre-Dream delega a ``snapshot_now("pre_dream")``, lo snapshot
di shutdown avviene DOPO il flush delle sessioni ed è fail-open.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jenny.runtime.container import GatewayContainer


class _RecordingSnapshot:
    """SnapshotService finto che registra le chiamate in una lista condivisa."""

    def __init__(self, order: list[str], *, fail_shutdown: bool = False) -> None:
        self._order = order
        self._fail_shutdown = fail_shutdown

    async def start(self) -> None:
        self._order.append("snapshot_start")

    def stop(self) -> None:
        self._order.append("snapshot_stop")

    async def snapshot_now(self, trigger: str, label: str | None = None):
        self._order.append(f"snapshot:{trigger}")
        if self._fail_shutdown and trigger == "shutdown":
            raise RuntimeError("snapshot di shutdown guasto")
        return None


class _FakeAgent:
    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.sessions = SimpleNamespace(flush_all=self._flush)

    def _flush(self) -> int:
        self._order.append("flush_all")
        return 0

    async def run(self) -> None:
        pass

    async def shutdown(self) -> None:
        self._order.append("agent_shutdown")


class _FakeChannels:
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def _bare_container(order: list[str], *, fail_shutdown: bool = False) -> GatewayContainer:
    """Container senza __init__: solo gli attributi che ``run()`` usa davvero."""
    container = GatewayContainer.__new__(GatewayContainer)
    # ``run()`` aggancia l'ingresso nativo al bus come prima cosa. Qui non c'è
    # un bus e non serve: ``bind_native_input`` lo dice e prosegue.
    container.bus = None
    container.cron = SimpleNamespace(start=AsyncMock(), stop=MagicMock())
    container.zone_watch = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    container.snapshot = _RecordingSnapshot(order, fail_shutdown=fail_shutdown)
    container.channels = _FakeChannels()
    container._agent = _FakeAgent(order)
    return container


async def test_snapshot_before_dream_triggers_pre_dream() -> None:
    order: list[str] = []
    container = GatewayContainer.__new__(GatewayContainer)
    container.snapshot = _RecordingSnapshot(order)
    await container._snapshot_before_dream()
    assert order == ["snapshot:pre_dream"]


async def test_snapshot_before_dream_noop_without_service() -> None:
    container = GatewayContainer.__new__(GatewayContainer)
    container.snapshot = None
    await container._snapshot_before_dream()  # nessun errore


async def test_take_snapshot_passes_the_trigger_through() -> None:
    """Il meccanismo generico dietro i checkpoint pre-lavoro. I contratti sopra
    restano due — Dream promette al modello la reversibilità, il giardiniere no —
    ma l'implementazione è una."""
    order: list[str] = []
    container = GatewayContainer.__new__(GatewayContainer)
    container.snapshot = _RecordingSnapshot(order)

    assert await container._take_snapshot("pre_gardener") is True
    assert order == ["snapshot:pre_gardener"]


async def test_take_snapshot_says_false_when_snapshots_are_off() -> None:
    """«Nel dubbio si mente al ribasso»: con gli snapshot spenti il checkpoint
    **non** è avvenuto, e dichiararlo avvenuto attaccherebbe a Dream una
    rassicurazione falsa proprio nella frase che serve a fargli cancellare."""
    container = GatewayContainer.__new__(GatewayContainer)
    container.snapshot = None

    assert await container._take_snapshot("pre_gardener") is False


def test_the_agent_is_handed_the_generic_hook() -> None:
    """Il giardiniere trova il gancio con ``getattr``, perché fuori dal gateway
    non c'è. Quel ``getattr`` è sicuro **solo** se in produzione qualcuno lo
    collega: questo test è la garanzia, e senza di lui una passata girerebbe per
    sempre senza rete senza che nulla lo dica."""
    import inspect

    source = inspect.getsource(GatewayContainer)

    assert "agent.take_snapshot = self._take_snapshot" in source


async def test_run_takes_shutdown_snapshot_after_flush() -> None:
    """Lo snapshot finale deve fotografare le sessioni GIÀ flushate su disco."""
    order: list[str] = []
    container = _bare_container(order)
    await container.run()
    assert "snapshot:shutdown" in order
    assert order.index("flush_all") < order.index("snapshot:shutdown")
    # Il drain dell'agente precede il flush (nessun writer attivo durante il flush).
    assert order.index("agent_shutdown") < order.index("flush_all")
    assert order[-1] == "snapshot:shutdown"


async def test_run_shutdown_snapshot_failure_is_swallowed() -> None:
    """Uno snapshot di shutdown guasto non deve far fallire lo shutdown."""
    order: list[str] = []
    container = _bare_container(order, fail_shutdown=True)
    await container.run()  # non solleva
    assert order[-1] == "snapshot:shutdown"
