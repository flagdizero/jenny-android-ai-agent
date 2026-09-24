"""Fase 1.2 — Code del bus limitate + backpressure.

- Default illimitato: `try_publish_outbound` non scarta mai (comportamento storico).
- Outbound limitata: i transient vengono scartati quando piena (`try_publish_outbound`),
  mentre i finali bloccano finché c'è spazio (`publish_outbound`).
- Inbound limitata: il produttore blocca a capienza (backpressure corretta).
"""

from __future__ import annotations

import asyncio

from jenny.bus.events import InboundMessage, OutboundMessage
from jenny.bus.queue import MessageBus


def _out(content: str = "x") -> OutboundMessage:
    return OutboundMessage(channel="websocket", chat_id="c", content=content)


def _in(content: str = "x") -> InboundMessage:
    return InboundMessage(channel="websocket", sender_id="u", chat_id="c", content=content)


async def test_unbounded_default_never_drops() -> None:
    bus = MessageBus()
    for i in range(1000):
        assert bus.try_publish_outbound(_out(str(i))) is True
    assert bus.outbound_size == 1000


async def test_bounded_outbound_drops_transient_when_full() -> None:
    bus = MessageBus(outbound_maxsize=2)
    assert bus.try_publish_outbound(_out("a")) is True
    assert bus.try_publish_outbound(_out("b")) is True
    assert bus.try_publish_outbound(_out("c")) is False  # piena → scartato
    assert bus.outbound_size == 2


async def test_bounded_outbound_final_blocks_until_space() -> None:
    bus = MessageBus(outbound_maxsize=1)
    await bus.publish_outbound(_out("a"))  # riempie

    pub = asyncio.create_task(bus.publish_outbound(_out("b")))
    await asyncio.sleep(0.02)
    assert not pub.done(), "publish_outbound dovrebbe bloccare quando pieno"

    got = await bus.consume_outbound()
    assert got.content == "a"
    await asyncio.wait_for(pub, timeout=1.0)
    assert pub.done()


async def test_bounded_inbound_blocks_producer_at_capacity() -> None:
    bus = MessageBus(inbound_maxsize=1)
    await bus.publish_inbound(_in("a"))

    pub = asyncio.create_task(bus.publish_inbound(_in("b")))
    await asyncio.sleep(0.02)
    assert not pub.done(), "publish_inbound dovrebbe bloccare a capienza"

    await bus.consume_inbound()
    await asyncio.wait_for(pub, timeout=1.0)
    assert pub.done()
