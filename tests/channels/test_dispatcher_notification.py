"""Il canale della tendina visto dal dispatcher.

Qui si prova la cosa che rende la notifica *un canale* invece di una consegna
scritta a lato: la risposta viene postata una volta sola e, nello stesso giro,
proiettata sulla vista WebUI con la provenienza — che è il marcatore su cui
``ws_sender`` decide di **non** far squillare una seconda notifica.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest

from jenny.bus.events import NOTIFICATION_CHANNEL, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels import notification as nc
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.channels.notification import REPLY_THREAD_TAG, NotificationChannel
from jenny.config.schema import Config


class MockWebSocket:
    name = "websocket"
    send_progress = True
    send_tool_hints = True
    show_reasoning = False
    send_max_retries = 1

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage, *, only_conns=None, skip_persist=False):
        self.sent.append(msg)
        return []


class _Alerts:
    def __init__(self) -> None:
        self.posted: list[tuple[str, str | None]] = []

    async def __call__(self, content, metadata, *, thread=None):
        self.posted.append((content, thread))
        return True


@pytest.fixture
def alerts(monkeypatch) -> _Alerts:
    spy = _Alerts()
    monkeypatch.setattr(nc, "post_alert", spy)
    return spy


def _wired() -> tuple[WebSocketDispatcher, MockWebSocket]:
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockWebSocket()
    d.channels["websocket"] = ws
    d.channels[NOTIFICATION_CHANNEL] = NotificationChannel()
    return d, ws


async def _pump(d: WebSocketDispatcher, until, timeout: float = 1.0) -> None:
    task = asyncio.create_task(d._dispatch_outbound())
    try:
        for _ in range(int(timeout / 0.005)):
            if until():
                break
            await asyncio.sleep(0.005)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_the_answer_rings_once_and_ends_up_in_chat(alerts: _Alerts) -> None:
    d, ws = _wired()
    await d.bus.publish_outbound(
        OutboundMessage(channel=NOTIFICATION_CHANNEL, chat_id="shade", content="fatto")
    )

    await _pump(d, lambda: alerts.posted and ws.sent)

    # Un solo alert, sul thread della conversazione.
    assert alerts.posted == [("fatto", REPLY_THREAD_TAG)]
    # E la stessa riga proiettata sulla vista canonica.
    (mirror,) = ws.sent
    assert mirror.chat_id == "default"
    assert mirror.content == "fatto"
    # `origin_channel` è ciò che tiene muto ws_sender (v. ws_sender.py, ramo
    # `phase == "answer" and not payload.get("origin")`): senza questo marcatore
    # la stessa risposta squillerebbe due volte.
    assert mirror.metadata["origin_channel"] == NOTIFICATION_CHANNEL


async def test_a_progress_does_not_reach_the_dropdown(alerts: _Alerts) -> None:
    """Gating per-canale: ``send_progress = False``, quindi il dispatcher
    scarta prima di instradare."""
    d, _ws = _wired()
    await d.bus.publish_outbound(
        OutboundMessage(
            channel=NOTIFICATION_CHANNEL, chat_id="shade", content="sto pensando",
            metadata={"_progress": True},
        )
    )
    await _pump(d, lambda: False, timeout=0.15)
    assert alerts.posted == []


async def test_the_turn_end_neither_rings_nor_is_projected(alerts: _Alerts) -> None:
    d, ws = _wired()
    await d.bus.publish_outbound(
        OutboundMessage(
            channel=NOTIFICATION_CHANNEL, chat_id="shade", content="",
            metadata={"_turn_end": True},
        )
    )
    await _pump(d, lambda: False, timeout=0.15)
    assert alerts.posted == []
    assert ws.sent == []


async def test_without_registered_channel_the_final_goes_nowhere(
    alerts: _Alerts,
) -> None:
    """Fuori da Android il canale non esiste — e là nessuno può produrre
    l'inbound che genera questo outbound. Il messaggio cade su "Unknown
    channel" senza abbattere il pump."""
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockWebSocket()
    d.channels["websocket"] = ws

    await d.bus.publish_outbound(
        OutboundMessage(channel=NOTIFICATION_CHANNEL, chat_id="shade", content="fatto")
    )
    await _pump(d, lambda: False, timeout=0.15)

    assert alerts.posted == []
    assert ws.sent == []


async def test_the_channel_is_not_born_without_an_android_context() -> None:
    """La registrazione è gated sul contesto: in CI e su desktop non c'è."""
    d = WebSocketDispatcher(Config(), MessageBus())
    assert NOTIFICATION_CHANNEL not in d.channels


async def test_the_channel_is_born_with_an_android_context(monkeypatch) -> None:
    monkeypatch.setattr("jenny.runtime.context.get_android_context", lambda: object())
    d = WebSocketDispatcher(Config(), MessageBus())
    assert isinstance(d.channels.get(NOTIFICATION_CHANNEL), NotificationChannel)


def test_the_channel_is_not_a_target_of_the_proactive_fanout() -> None:
    """Test negativo, e serve.

    Gli ``extra_targets`` sono i destinatari del fan-out **proattivo**: mettere
    la tendina là dentro farebbe squillare ogni promemoria del cron due volte —
    una dal percorso WebUI (``notify_delivery``) e una da qui. Oggi si evita non
    aggiungendo una riga, il che vuol dire che nessun test se ne accorgerebbe:
    questo lo fa, chiedendo i target a fabbrica piena.
    """
    from jenny.runtime.container import GatewayContainer

    class _Dispatcher:
        channels = {
            "websocket": object(),
            "telegram": type("Tg", (), {"paired_chat_id": "42"})(),
            NOTIFICATION_CHANNEL: NotificationChannel(),
        }

    fake: Any = type("C", (), {"channels": _Dispatcher()})()
    assert GatewayContainer._telegram_targets(fake) == [("telegram", "42")]
