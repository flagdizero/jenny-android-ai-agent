"""Quante richieste al provider possono essere in volo insieme.

Un provider che regge N richieste insieme (``LLMProvider.max_concurrent_requests``)
le rifiuta con un 429 oltre la soglia. Il 04/11/2026, col gateway della Play a 2,
tre aiutanti e Jenny ci sbattevano contro: le riprove in background non sono
bastate, e un aiutante al 14° passo è morto ed è stato rifatto da zero. Qui chi
eccede aspetta il suo posto invece di arrivare al server.

Jenny che risponde all'utente passa davanti: il lavoro in background (gli
aiutanti) ne occupa al più N−1, così un posto resta sempre per lei.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class RequestSlots:
    """N posti, di cui al più N−1 per il lavoro in background (con N ≥ 2)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.in_flight = 0
        self.background = 0
        self._cond: asyncio.Condition | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _condition(self) -> asyncio.Condition:
        # Una Condition si lega al loop su cui si usa: dopo un riavvio del
        # gateway nello stesso processo ne serve una nuova, e i conti ripartono.
        loop = asyncio.get_running_loop()
        if self._cond is None or self._loop is not loop:
            self._cond = asyncio.Condition()
            self._loop = loop
            self.in_flight = self.background = 0
        return self._cond

    def _free(self, background: bool) -> bool:
        if self.in_flight >= self.limit:
            return False
        return not (background and self.limit >= 2 and self.background >= self.limit - 1)

    @asynccontextmanager
    async def hold(self, background: bool) -> AsyncIterator[None]:
        cond = self._condition()
        async with cond:
            await cond.wait_for(lambda: self._free(background))
            self.in_flight += 1
            if background:
                self.background += 1
        try:
            yield
        finally:
            async with cond:
                self.in_flight -= 1
                if background:
                    self.background -= 1
                cond.notify_all()
