"""Fase 1.5 — La race di injection è chiusa.

Prima: `run()` controllava `effective_key in self._pending_queues` ma `_dispatch`
registrava la coda solo DOPO aver preso il lock; un secondo messaggio arrivato
nella finestra tra `create_task` e la registrazione generava un turno
competitivo invece di essere iniettato. Ora `run()` registra la coda PRIMA di
`create_task` (atomico nel consumer singolo) → il follow-up viene iniettato.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from jenny.bus.events import InboundMessage


def _msg(content: str) -> InboundMessage:
    return InboundMessage(
        channel="websocket", sender_id="u", chat_id="chat-1", content=content
    )


async def test_second_message_is_injected_not_a_competing_task(loop_factory) -> None:
    loop = loop_factory()

    call_count = 0
    first_running = asyncio.Event()
    release = asyncio.Event()
    injected: list[str] = []

    async def fake_process(msg, *, pending_queue=None, **kwargs):
        nonlocal call_count
        call_count += 1
        first_running.set()
        await release.wait()
        while pending_queue is not None and not pending_queue.empty():
            injected.append(pending_queue.get_nowait().content)
        return None

    loop._process_message = fake_process  # type: ignore[method-assign]
    loop._running = True

    run_task = asyncio.create_task(loop.run())
    try:
        await loop.bus.publish_inbound(_msg("first"))
        await asyncio.wait_for(first_running.wait(), timeout=1.0)

        # Secondo messaggio per la stessa sessione mentre il primo è "in corso".
        await loop.bus.publish_inbound(_msg("second"))
        await asyncio.sleep(0.05)  # dà a run() il tempo di instradarlo

        # Nessun turno competitivo: _process_message chiamato una sola volta.
        assert call_count == 1

        release.set()
        await asyncio.sleep(0.05)

        # Il follow-up è stato iniettato nella pending-queue del turno attivo.
        assert injected == ["second"]
    finally:
        loop.stop()
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


async def _two_messages(loop_factory, first: InboundMessage, second: InboundMessage):
    """Manda *second* mentre il turno di *first* è in volo: chi è entrato dove."""
    loop = loop_factory()
    turns: list[str] = []
    injected: list[str] = []
    first_running = asyncio.Event()
    release = asyncio.Event()

    async def fake_process(msg, *, pending_queue=None, **kwargs):
        turns.append(f"{msg.channel}:{msg.content}")
        if len(turns) == 1:
            first_running.set()
            await release.wait()
        while pending_queue is not None and not pending_queue.empty():
            injected.append(pending_queue.get_nowait().content)
        return None

    loop._process_message = fake_process  # type: ignore[method-assign]
    loop._running = True
    run_task = asyncio.create_task(loop.run())
    try:
        await loop.bus.publish_inbound(first)
        await asyncio.wait_for(first_running.wait(), timeout=1.0)
        await loop.bus.publish_inbound(second)
        await asyncio.sleep(0.05)
        assert len(turns) == 1, "il secondo non parte finché il primo è in volo"
        release.set()
        await asyncio.sleep(0.1)
        return turns, injected
    finally:
        loop.stop()
        run_task.cancel()
        with suppress(asyncio.CancelledError):
            await run_task


def _from(channel: str, content: str, chat_id: str = "default") -> InboundMessage:
    return InboundMessage(channel=channel, sender_id="u", chat_id=chat_id, content=content)


async def test_a_message_from_another_channel_waits_for_its_own_turn(loop_factory) -> None:
    """Un turno risponde sul canale che l'ha aperto. Un messaggio scritto da
    Telegram mentre la WebUI ha un turno in volo, messo dentro quel turno,
    avrebbe la risposta sulla WebUI: chi ha scritto da Telegram non la vede."""
    turns, injected = await _two_messages(
        loop_factory, _from("websocket", "dalla casa"), _from("telegram", "da telegram", "42")
    )
    assert injected == []
    assert turns == ["websocket:dalla casa", "telegram:da telegram"]


async def test_a_helper_result_still_enters_the_turn_in_flight(loop_factory) -> None:
    """Il rientro di un aiutante non è scritto dall'utente: entra come prima."""
    turns, injected = await _two_messages(
        loop_factory,
        _from("websocket", "dalla casa"),
        InboundMessage(
            channel="system", sender_id="subagent", chat_id="websocket:default",
            content="risultato", session_key_override="unified:default",
        ),
    )
    assert injected == ["risultato"]
    assert turns == ["websocket:dalla casa"]
