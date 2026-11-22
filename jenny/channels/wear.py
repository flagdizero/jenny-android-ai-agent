"""Canale dell'orologio: la risposta a una domanda detta al polso.

Quarto canale nativo dopo la tendina e la mascotte, e fatto come loro: un
:class:`FinalTextChannel`, quindi niente spinner, tool hint o pezzi di
risposta — all'orologio arriva il testo finale e basta. Su un cerchio di
quattro centimetri una risposta che si scrive a pezzi non si legge, e ogni
pezzo sarebbe un messaggio Bluetooth in più.

Essere un canale gli fa ereditare quello che la tendina spiega per esteso
(``channels/notification.py``): la conversazione unica, l'eco della domanda in
chat, la risposta nel transcript. Chi apre l'app sul telefono trova quello che
si è detto al polso, al suo posto.

Come la mascotte, il canale **non** entra fra gli ``extra_targets`` del
``ChannelDeliverer``: un promemoria del cron non deve finire su un orologio che
nel frattempo ha chiuso l'app. Un job chiesto dal polso consegna nella chat
personale (``cron/bound_runner.origin_delivery_context``).
"""

from __future__ import annotations

from typing import Any

from jenny.bus.events import WEAR_CHANNEL
from jenny.channels.non_streaming import FinalTextChannel
from jenny.runtime.native_input import NATIVE_THREAD_KEY
from jenny.runtime.wear import send_reply


class WearChannel(FinalTextChannel):
    """Consegna la risposta all'orologio che ha fatto la domanda."""

    name = WEAR_CHANNEL
    target = "the watch"

    async def _deliver(self, content: str, metadata: dict[str, Any]) -> bool:
        """Manda il testo all'indirizzo con cui è entrata la domanda. ``False``
        è un esito normale (orologio lontano, app sul polso chiusa): l'orologio
        se ne accorge da solo, perché aspetta la risposta con un tempo massimo."""
        raw = metadata.get(NATIVE_THREAD_KEY)
        address = raw.strip() if isinstance(raw, str) else None
        return await send_reply(content, address)
