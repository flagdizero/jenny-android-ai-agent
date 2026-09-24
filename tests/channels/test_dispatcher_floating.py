"""Il canale della mascotte flottante visto dal dispatcher.

Qui si prova la cosa che rende il fumetto *un canale* invece di una consegna
scritta a lato: la risposta viene disegnata una volta sola e, nello stesso giro,
proiettata sulla vista WebUI con la provenienza — che è il marcatore su cui
``ws_sender`` decide di **non** far squillare una notifica per parole che
l'utente sta già leggendo sopra la testa della mascotte.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest

from jenny.bus.events import FLOATING_CHANNEL, OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels import floating as fc
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.channels.floating import FloatingChannel
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


class _Bubbles:
    def __init__(self) -> None:
        self.shown: list[str] = []

    async def __call__(self, text: str) -> bool:
        self.shown.append(text)
        return True


@pytest.fixture
def bubbles(monkeypatch) -> _Bubbles:
    spy = _Bubbles()
    monkeypatch.setattr(fc, "show_reply", spy)
    return spy


def _wired() -> tuple[WebSocketDispatcher, MockWebSocket]:
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockWebSocket()
    d.channels["websocket"] = ws
    d.channels[FLOATING_CHANNEL] = FloatingChannel()
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


async def test_la_risposta_si_disegna_una_volta_e_finisce_in_chat(bubbles: _Bubbles) -> None:
    d, ws = _wired()
    await d.bus.publish_outbound(
        OutboundMessage(channel=FLOATING_CHANNEL, chat_id="shade", content="fatto")
    )

    await _pump(d, lambda: bubbles.shown and ws.sent)

    # Un solo fumetto.
    assert bubbles.shown == ["fatto"]
    # E la stessa riga proiettata sulla vista canonica: chiedere dalla mascotte
    # non apre una conversazione a parte, scrive in quella dell'app.
    (mirror,) = ws.sent
    assert mirror.chat_id == "default"
    assert mirror.content == "fatto"
    # `origin_channel` è ciò che tiene muto ws_sender (v. ws_sender.py, ramo
    # `phase == "answer" and not payload.get("origin")`): senza questo marcatore
    # la risposta comparirebbe nel fumetto **e** squillerebbe in tendina.
    assert mirror.metadata["origin_channel"] == FLOATING_CHANNEL


async def test_un_progress_non_arriva_al_fumetto(bubbles: _Bubbles) -> None:
    """Gating per-canale: ``send_progress = False``, quindi il dispatcher
    scarta prima di instradare. Mentre aspetta, lo stato lo dice la faccia."""
    d, _ws = _wired()
    await d.bus.publish_outbound(
        OutboundMessage(
            channel=FLOATING_CHANNEL, chat_id="shade", content="sto pensando",
            metadata={"_progress": True},
        )
    )
    await _pump(d, lambda: False, timeout=0.15)
    assert bubbles.shown == []


async def test_il_turn_end_non_disegna_e_non_si_proietta(bubbles: _Bubbles) -> None:
    d, ws = _wired()
    await d.bus.publish_outbound(
        OutboundMessage(
            channel=FLOATING_CHANNEL, chat_id="shade", content="",
            metadata={"_turn_end": True},
        )
    )
    await _pump(d, lambda: False, timeout=0.15)
    assert bubbles.shown == []
    assert ws.sent == []


async def test_senza_canale_registrato_il_finale_non_va_da_nessuna_parte(
    bubbles: _Bubbles,
) -> None:
    """Fuori da Android il canale non esiste — e là nessuno può produrre
    l'inbound che genera questo outbound. Il messaggio cade su "Unknown
    channel" senza abbattere il pump."""
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockWebSocket()
    d.channels["websocket"] = ws

    await d.bus.publish_outbound(
        OutboundMessage(channel=FLOATING_CHANNEL, chat_id="shade", content="fatto")
    )
    await _pump(d, lambda: False, timeout=0.15)

    assert bubbles.shown == []
    assert ws.sent == []


async def test_il_canale_non_nasce_senza_contesto_android() -> None:
    """La registrazione è gated sul contesto: in CI e su desktop non c'è."""
    d = WebSocketDispatcher(Config(), MessageBus())
    assert FLOATING_CHANNEL not in d.channels


async def test_il_canale_nasce_con_un_contesto_android(monkeypatch) -> None:
    monkeypatch.setattr("jenny.runtime.context.get_android_context", lambda: object())
    d = WebSocketDispatcher(Config(), MessageBus())
    assert isinstance(d.channels.get(FLOATING_CHANNEL), FloatingChannel)


async def test_il_canale_nasce_anche_a_mascotte_spenta(monkeypatch) -> None:
    """La registrazione **non** guarda ``floating.enabled``, ed è deliberato.

    Quel flag decide se esiste la *finestra*, e la risposta ce l'ha Kotlin.
    Legare il canale al flag creerebbe il guasto vero: accesa dalle impostazioni
    a gateway già su, la mascotte avrebbe la finestra e nessun canale — cioè un
    campo che accetta testo e una risposta che non torna mai.
    """
    monkeypatch.setattr("jenny.runtime.context.get_android_context", lambda: object())
    cfg = Config()
    assert cfg.floating.enabled is False
    d = WebSocketDispatcher(cfg, MessageBus())
    assert isinstance(d.channels.get(FLOATING_CHANNEL), FloatingChannel)


def test_il_canale_non_e_un_bersaglio_del_fanout_proattivo() -> None:
    """Test negativo, e serve.

    Gli ``extra_targets`` sono i destinatari del fan-out **proattivo**: mettere
    la mascotte là dentro farebbe stampare ogni promemoria del cron sopra l'app
    che l'utente sta usando in quel momento. Oggi si evita non aggiungendo una
    riga, il che vuol dire che nessun test se ne accorgerebbe: questo lo fa,
    chiedendo i target a fabbrica piena.
    """
    from jenny.runtime.container import GatewayContainer

    class _Dispatcher:
        channels = {
            "websocket": object(),
            "telegram": type("Tg", (), {"paired_chat_id": "42"})(),
            FLOATING_CHANNEL: FloatingChannel(),
        }

    fake: Any = type("C", (), {"channels": _Dispatcher()})()
    assert GatewayContainer._telegram_targets(fake) == [("telegram", "42")]
