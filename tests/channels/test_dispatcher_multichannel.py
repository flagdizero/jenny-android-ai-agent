"""Test del routing multi-canale del dispatcher (websocket + telegram)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from support.aio import queue_idle, wait_until

from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels.dispatcher import WebSocketDispatcher
from jenny.config.schema import Config


class MockChannel:
    def __init__(self, name: str, *, send_progress: bool = True) -> None:
        self.name = name
        self.send_progress = send_progress
        self.send_tool_hints = send_progress
        self.show_reasoning = False
        self.send_max_retries = 1
        self.sent: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage, *, only_conns=None, skip_persist=False):
        self.sent.append(msg)
        return []


async def _pump(d: WebSocketDispatcher, until=None, timeout: float = 1.0) -> None:
    """Fa girare il dispatcher finché *until()* è vero e, senza *until*, finché non
    ha smaltito la coda ed è tornato ad aspettare (``queue_idle``): così un test
    negativo («non è stato mandato niente») guarda un messaggio lavorato davvero.
    Alla scadenza fallisce, invece di proseguire (v. ``support.aio``)."""
    task = asyncio.create_task(d._dispatch_outbound())
    try:
        if until is None:
            await wait_until(lambda: queue_idle(d.bus.outbound), timeout=timeout,
                             msg="il dispatcher non ha smaltito la coda")
        else:
            await wait_until(until, timeout=timeout)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_routes_by_channel_name_and_mirrors_final_on_webui_view() -> None:
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockChannel("websocket")
    tg = MockChannel("telegram", send_progress=False)
    d.channels["websocket"] = ws
    d.channels["telegram"] = tg

    await d.bus.publish_outbound(OutboundMessage(channel="telegram", chat_id="42", content="a"))
    await d.bus.publish_outbound(OutboundMessage(channel="websocket", chat_id="default", content="b"))

    await _pump(d, lambda: len(ws.sent) >= 2 and tg.sent)

    assert [m.content for m in tg.sent] == ["a"]
    # Il finale telegram viene anche proiettato sulla vista WebUI (chat default)
    # con la provenienza nei metadata; il messaggio websocket resta com'è.
    assert [m.content for m in ws.sent] == ["a", "b"]
    mirror = ws.sent[0]
    assert mirror.chat_id == "default"
    assert mirror.metadata["origin_channel"] == "telegram"
    assert "origin_channel" not in ws.sent[1].metadata


async def test_no_webui_mirror_for_proactive_mirror_copies_and_non_finals() -> None:
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockChannel("websocket")
    tg = MockChannel("telegram", send_progress=False)
    d.channels["websocket"] = ws
    d.channels["telegram"] = tg

    # Copia _mirror del fan-out proattivo: la primaria websocket è già stata
    # pubblicata dal ChannelDeliverer, nessuna seconda proiezione.
    await d.bus.publish_outbound(
        OutboundMessage(
            channel="telegram", chat_id="42", content="promemoria",
            metadata={"_mirror": True},
        )
    )
    # turn_end e contenuto vuoto: eventi di coordinamento, mai proiettati.
    await d.bus.publish_outbound(
        OutboundMessage(
            channel="telegram", chat_id="42", content="",
            metadata={"_turn_end": True},
        )
    )
    # Sentinella finale per sapere quando il pump ha processato tutto.
    await d.bus.publish_outbound(OutboundMessage(channel="telegram", chat_id="42", content="fine"))

    await _pump(d, lambda: len(tg.sent) >= 3)

    assert [m.content for m in ws.sent] == ["fine"]
    assert ws.sent[0].metadata["origin_channel"] == "telegram"


async def test_unknown_channel_dropped_without_crash() -> None:
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockChannel("websocket")
    d.channels["websocket"] = ws

    await d.bus.publish_outbound(OutboundMessage(channel="ghost", chat_id="1", content="x"))
    await d.bus.publish_outbound(OutboundMessage(channel="websocket", chat_id="1", content="ok"))

    await _pump(d, lambda: ws.sent)

    assert [m.content for m in ws.sent] == ["ok"]


async def test_progress_not_delivered_to_channel_that_declines() -> None:
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockChannel("websocket", send_progress=True)
    tg = MockChannel("telegram", send_progress=False)
    d.channels["websocket"] = ws
    d.channels["telegram"] = tg

    meta: dict[str, Any] = {"_progress": True}
    await d.bus.publish_outbound(
        OutboundMessage(channel="telegram", chat_id="42", content="hint", metadata=dict(meta))
    )
    await d.bus.publish_outbound(
        OutboundMessage(channel="websocket", chat_id="1", content="hint", metadata=dict(meta))
    )

    await _pump(d, lambda: ws.sent)

    assert tg.sent == []
    assert len(ws.sent) == 1


_START = [{"version": 1, "phase": "start", "call_id": "c1", "name": "web_search"}]


async def test_tool_start_reaches_a_channel_without_tool_hints_minus_the_hint() -> None:
    """Con ``sendToolHints`` spento il suggerimento resta fuori, gli strumenti no.

    Il ``tool_hint`` e' l'unico frame che dice quali strumenti partono: buttarlo
    intero lasciava alla WebUI i soli ``end``, e la riga di lavoro della casa
    non si riaccendeva mai durante un giro di strumenti.
    """
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockChannel("websocket")
    ws.send_tool_hints = False
    d.channels["websocket"] = ws

    await d.bus.publish_outbound(
        OutboundMessage(
            channel="websocket", chat_id="project:orto", content='web_search("gatti")',
            metadata={"_progress": True, "_tool_hint": True, "_tool_events": _START},
        )
    )
    await _pump(d, lambda: ws.sent)

    assert len(ws.sent) == 1
    sent = ws.sent[0]
    assert sent.content == ""
    assert sent.chat_id == "project:orto"
    assert sent.metadata["_tool_hint"] is False
    assert sent.metadata["_progress"] is True
    assert sent.metadata["_tool_events"] == _START


async def test_a_bare_tool_hint_stays_dropped_and_progress_gating_still_wins() -> None:
    """Un suggerimento senza strumenti resta scartato come prima, e dove i
    progress sono spenti (Telegram) non passano nemmeno gli strumenti."""
    d = WebSocketDispatcher(Config(), MessageBus())
    ws = MockChannel("websocket")
    ws.send_tool_hints = False
    tg = MockChannel("telegram", send_progress=False)
    d.channels["websocket"] = ws
    d.channels["telegram"] = tg

    await d.bus.publish_outbound(
        OutboundMessage(
            channel="websocket", chat_id="1", content="subagent started: fix parser",
            metadata={"_progress": True, "_tool_hint": True},
        )
    )
    await d.bus.publish_outbound(
        OutboundMessage(
            channel="telegram", chat_id="42", content="web_search(…)",
            metadata={"_progress": True, "_tool_hint": True, "_tool_events": _START},
        )
    )
    await d.bus.publish_outbound(OutboundMessage(channel="websocket", chat_id="1", content="fine"))
    await _pump(d, lambda: ws.sent)

    assert [m.content for m in ws.sent] == ["fine"]
    assert tg.sent == []


def test_coordination_flags_are_single_source_for_both_lists() -> None:
    """Le due liste di flag di coordinamento derivano dalla stessa costante.

    Il dispatcher aggiunge solo ``_mirror``; Telegram usa il core così com'è.
    Un drift fra le liste rompe questo test.
    """
    from jenny.bus.events import COORDINATION_FLAGS
    from jenny.channels.dispatcher import _NON_FINAL_METADATA_FLAGS
    from jenny.channels.telegram import TelegramChannel

    assert set(_NON_FINAL_METADATA_FLAGS) == set(COORDINATION_FLAGS) | {"_mirror"}
    # Telegram deriva letteralmente dal core, senza ``_mirror``.
    sample = {flag: True for flag in COORDINATION_FLAGS}
    for flag in COORDINATION_FLAGS:
        assert TelegramChannel._is_webui_only_event({flag: True}) is True
    assert TelegramChannel._is_webui_only_event(sample) is True
    assert TelegramChannel._is_webui_only_event({"_mirror": True}) is False
    # Il path morto goal_state è stato rimosso dalla tassonomia.
    assert "_goal_state_sync" not in COORDINATION_FLAGS
    assert "_goal_state_sync" not in _NON_FINAL_METADATA_FLAGS


def test_telegram_channel_not_built_without_token() -> None:
    config = Config()
    config.telegram.enabled = True  # ma senza token
    d = WebSocketDispatcher(config, MessageBus())
    assert "telegram" not in d.channels
