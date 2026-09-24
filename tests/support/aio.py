"""Attese asincrone per i test: aspettare una condizione, e svuotare il bus.

Il ciclo «dormi un po' e ricontrolla» stava ricopiato in molti file, in due
forme. Quella con la scadenza a tempo e l'``assert`` finale era giusta. L'altra
— ``for _ in range(50): await asyncio.sleep(0.01); if …: break`` — alla fine
dei giri **proseguiva** comunque: se l'asserzione che seguiva era negativa
(«non è stato mandato niente»), passava anche quando il lavoro da verificare
non era mai partito. :func:`wait_until` alla scadenza fallisce, sempre.

Le scadenze le sceglie chi chiama: quelle che c'erano (1 s, 15 s) restano
scritte nei test, e non si abbassano.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


async def wait_until(
    predicate: Callable[[], Any],
    *,
    timeout: float = 5.0,
    interval: float = 0.01,
    msg: str | None = None,
) -> None:
    """Aspetta che *predicate()* sia vero; oltre *timeout* secondi, fallisce."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError(msg or f"condizione non raggiunta in {timeout}s")
        await asyncio.sleep(interval)


def queue_idle(queue: asyncio.Queue) -> bool:
    """La coda è vuota **e** chi la consuma è di nuovo fermo ad aspettare.

    ``qsize() == 0`` da solo non basta: dice che l'ultimo messaggio è stato
    tolto dalla coda, non che è stato lavorato — il consumatore può essere a
    metà. Un consumatore tornato in ``get()`` ha lasciato un getter in attesa
    (``Queue._getters``, privato ma stabile da Python 3.4): vuol dire che ha
    finito il giro.
    """
    return queue.qsize() == 0 and bool(getattr(queue, "_getters", None))


def drain_nowait(queue: asyncio.Queue) -> list:
    """Tutto quel che c'è in *queue* adesso, senza aspettare."""
    out = []
    while True:
        try:
            out.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return out
