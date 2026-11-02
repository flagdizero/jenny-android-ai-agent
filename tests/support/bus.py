"""Il ``MessageBus`` finto dei test: registra quel che riceve, nei due sensi.

Ce n'erano tre copie (la consegna, l'ingresso dalla tendina, il piano), ognuna
con un pezzo: qui l'unione.

- ``inbound`` / ``outbound``: i messaggi pubblicati, in ordine;
- ``arrived``: si accende al primo inbound, per chi pubblica da un altro thread
  e deve aspettare che il messaggio arrivi davvero;
- ``raise_error``: ogni ``publish_outbound`` solleva quell'eccezione, come un
  canale rotto, e non registra niente.
"""

from __future__ import annotations

import asyncio
from typing import Any


class FakeBus:
    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self.inbound: list[Any] = []
        self.outbound: list[Any] = []
        self.arrived = asyncio.Event()
        self._raise_error = raise_error

    async def publish_inbound(self, msg: Any) -> None:
        self.inbound.append(msg)
        self.arrived.set()

    async def publish_outbound(self, msg: Any) -> None:
        if self._raise_error is not None:
            raise self._raise_error
        self.outbound.append(msg)
