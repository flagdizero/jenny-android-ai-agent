"""Async message queue for decoupled channel-agent communication."""

import asyncio

from loguru import logger

from jenny.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    Le code possono essere limitate (``maxsize``) per fornire backpressure su
    dispositivi con poca memoria (Android). ``maxsize <= 0`` significa coda
    illimitata (comportamento storico, usato di default e in tutti i test).
    """

    def __init__(self, *, inbound_maxsize: int = 0, outbound_maxsize: int = 0):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(
            maxsize=max(0, inbound_maxsize)
        )
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(
            maxsize=max(0, outbound_maxsize)
        )
        self._dropped_outbound = 0

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent.

        Su coda limitata, blocca il produttore quando è piena: è la backpressure
        corretta (un agente lento deve rallentare l'intake dei messaggi)."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels (blocking).

        Da usare per i messaggi che NON devono mai essere persi (risposta finale,
        turn_end). Per i messaggi transient usare ``try_publish_outbound``."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    def try_publish_outbound(self, msg: OutboundMessage) -> bool:
        """Accoda senza bloccare; ritorna ``False`` (scartato) se la coda outbound
        è piena.

        Pensato per i messaggi transient (stream delta, progress, reasoning) su
        code limitate: perdere il live-preview è sicuro perché il messaggio
        finale autoritativo viene pubblicato a parte con ``publish_outbound``.
        Su coda illimitata (default) non scarta mai → identico a ``publish_outbound``.
        """
        try:
            self.outbound.put_nowait(msg)
            return True
        except asyncio.QueueFull:
            self._dropped_outbound += 1
            if self._dropped_outbound % 100 == 1:
                logger.warning(
                    "Outbound queue full; dropped {} transient message(s) so far",
                    self._dropped_outbound,
                )
            return False

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
