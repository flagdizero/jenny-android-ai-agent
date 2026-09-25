"""Fase 1.1 — Il pump outbound sopravvive a un messaggio "veleno".

Prima del fix, il corpo di `_dispatch_outbound` era in un solo `try` con solo
`except asyncio.CancelledError: break`: qualsiasi altra eccezione sfuggita a
`_send_with_retry` uccideva l'unica coroutine di consegna, bloccando per sempre
tutto l'outbound. Ora un'eccezione inattesa viene loggata e il messaggio scartato,
e il pump continua col successivo.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from unittest.mock import MagicMock

from support.aio import wait_until

import jenny.channels.ws_sender as ws_sender
from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.channels.ws_sender import OutboundSenderMixin
from jenny.config.schema import Config


async def test_pump_survives_poison_message_and_delivers_next() -> None:
    d = WebSocketDispatcher(Config(), MessageBus())
    # Sentinella non-None: _route_channel la ritorna per channel=="websocket".
    d.channels["websocket"] = object()

    delivered: list[str] = []

    async def fake_send_with_retry(channel, msg) -> None:
        if msg.chat_id == "poison":
            raise RuntimeError("boom")  # simula un bug sfuggito alla consegna
        delivered.append(msg.content)

    d._send_with_retry = fake_send_with_retry  # type: ignore[method-assign]

    task = asyncio.create_task(d._dispatch_outbound())
    try:
        await d.bus.publish_outbound(
            OutboundMessage(channel="websocket", chat_id="poison", content="x")
        )
        await d.bus.publish_outbound(
            OutboundMessage(channel="websocket", chat_id="chat-1", content="good")
        )

        await wait_until(
            lambda: "good" in delivered,
            timeout=1.0,
            interval=0.005,
            msg="il pump non ha consegnato il messaggio dopo il veleno",
        )
        assert not task.done(), "il pump è morto invece di continuare"
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# -- Fix 6: timeout su connection.send() (client lento stalla tutti) ----------
#
# `websockets` applica backpressure: un client col buffer TCP pieno blocca
# `send` a tempo indeterminato. Il dispatcher outbound è seriale, quindi un
# client zombie stallerebbe la consegna a *tutti*. `_fanout`
# ora avvolge il send con `asyncio.wait_for(..., timeout=_SEND_TIMEOUT_S)` e
# su timeout CHIUDONO e scartano la connessione (mai riusata: un send
# cancellato lascia un frame parziale sul filo), senza metterla in `pending`.


class _StubSender(OutboundSenderMixin):
    """Espone solo ciò che serve a `_fanout`."""

    def __init__(self) -> None:
        self._subs: dict[str, set[Any]] = {}
        self._conn_chats: dict[Any, set[str]] = {}
        self.logger = MagicMock()
        self.cleaned: list[Any] = []

    def _cleanup_connection(self, connection: Any) -> None:
        self.cleaned.append(connection)


class _FakeConn:
    """Connessione fake: `send` può bloccarsi per sempre o registrare il frame."""

    def __init__(self, *, block: bool) -> None:
        self.block = block
        self.received: list[str] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        if self.block:
            await asyncio.Event().wait()  # mai risolto → simula buffer TCP saturo
        self.received.append(raw)

    async def close(self) -> None:
        self.closed = True


async def test_fanout_times_out_slow_conn_and_still_delivers_to_others(monkeypatch) -> None:
    monkeypatch.setattr(ws_sender, "_SEND_TIMEOUT_S", 0.05)
    sender = _StubSender()
    good_a = _FakeConn(block=False)
    zombie = _FakeConn(block=True)
    good_b = _FakeConn(block=False)

    # (i) `_fanout` ritorna entro il timeout (bound generoso di sicurezza).
    pending = await asyncio.wait_for(
        sender._fanout([good_a, zombie, good_b], "frame"),
        timeout=2.0,
    )

    # (iii) le altre conn ricevono comunque il frame.
    assert good_a.received == ["frame"]
    assert good_b.received == ["frame"]
    # (iv) la conn in timeout NON è ritentata (non finisce in pending).
    assert zombie not in pending
    assert pending == []
    # (ii) la conn è stata ripulita e chiusa (fire-and-forget → lascia girare).
    assert zombie in sender.cleaned
    await asyncio.sleep(0)
    assert zombie.closed is True
