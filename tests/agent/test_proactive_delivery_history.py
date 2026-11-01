"""Un avviso proattivo deve finire nella cronologia della conversazione.

Regressione misurata sul dispositivo il 2026-08-12: l'heartbeat consegna
"pibox non è raggiungibile" alle 18:33 dalla sessione interna ``heartbeat``;
alle 18:39 l'utente chiede "sicura?" su ``unified:default`` e il modello
risponde come se non avesse mai detto nulla — perché nel contesto di quel
turno non c'era. Il testo veniva persistito nel transcript WebUI (che l'utente
vede) ma non nella history di sessione (che il modello legge).

Qui si verifica il contratto di ``AgentLoop.record_channel_delivery``: la riga
atterra nella sessione unificata, e la scrittura si serializza sul lock di
sessione invece di infilarsi in mezzo al blocco di un turno in volo.
"""

from __future__ import annotations

import asyncio

from jenny.bus.events import OutboundMessage
from jenny.runtime.delivery import ChannelDeliverer
from jenny.session.keys import UNIFIED_SESSION_KEY

ALERT = "pibox non è raggiungibile (Tailscale giù?) — controllo del backup non passato"


async def test_the_alert_lands_in_the_unified_history(loop_factory) -> None:
    loop = loop_factory()

    await loop.record_channel_delivery(session_key=UNIFIED_SESSION_KEY, content=ALERT)

    messages = loop.sessions.get_or_create(UNIFIED_SESSION_KEY).messages
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == ALERT
    assert messages[0]["_channel_delivery"] is True


async def test_the_next_turn_reads_the_alert_in_its_context(loop_factory) -> None:
    """Il punto della fix: la domanda dopo l'avviso arriva al modello con
    l'avviso dentro il contesto."""
    loop = loop_factory()

    await loop.record_channel_delivery(session_key=UNIFIED_SESSION_KEY, content=ALERT)

    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    session.add_message("user", "sicura?")
    history = session.get_history(max_messages=120, include_timestamps=True)

    assert any(ALERT in str(m.get("content")) for m in history)


async def test_media_are_kept_with_the_recorded_line(loop_factory) -> None:
    loop = loop_factory()
    media = ["/data/plots/backup.png"]

    await loop.record_channel_delivery(
        session_key=UNIFIED_SESSION_KEY, content="ecco il grafico", media=media
    )

    recorded = loop.sessions.get_or_create(UNIFIED_SESSION_KEY).messages[0]
    assert recorded["media"] == media
    assert recorded["media"] is not media  # copia, non la lista del chiamante


async def test_a_blank_alert_is_not_recorded(loop_factory) -> None:
    loop = loop_factory()

    await loop.record_channel_delivery(session_key=UNIFIED_SESSION_KEY, content="  \n ")

    assert loop.sessions.get_or_create(UNIFIED_SESSION_KEY).messages == []


async def test_recording_waits_for_a_turn_in_flight(loop_factory) -> None:
    """Con un turno in volo la scrittura non deve infilarsi tra i suoi messaggi:
    ``_save_turn`` appende il blocco del turno in coda, e un assistant estraneo
    in mezzo alla coppia ``tool_calls``/``tool`` è una richiesta illegale per il
    provider. Il lock di sessione la mette dopo il blocco del turno."""
    loop = loop_factory()
    key = UNIFIED_SESSION_KEY
    lock = loop._session_locks.get(key)
    session = loop.sessions.get_or_create(key)

    turn_holds_lock = asyncio.Event()
    let_the_turn_finish = asyncio.Event()

    async def turn() -> None:
        async with lock:
            session.add_message("user", "sicura?")
            turn_holds_lock.set()
            await let_the_turn_finish.wait()
            # Blocco di fine turno, appeso in coda come fa ``_save_turn``.
            session.messages.append(
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}
            )
            session.messages.append({"role": "tool", "tool_call_id": "c1", "content": "ok"})
            session.add_message("assistant", "confermato")

    turn_task = asyncio.create_task(turn())
    await asyncio.wait_for(turn_holds_lock.wait(), timeout=1.0)

    await loop.record_channel_delivery(session_key=key, content=ALERT)
    # Ritorna subito senza bloccare il chiamante (è un tool in esecuzione).
    assert [m.get("content") for m in session.messages] == ["sicura?"]

    let_the_turn_finish.set()
    await asyncio.wait_for(turn_task, timeout=1.0)
    await asyncio.wait_for(asyncio.gather(*loop._background_tasks), timeout=1.0)

    roles = [(m["role"], m.get("content")) for m in session.messages]
    assert roles == [
        ("user", "sicura?"),
        ("assistant", ""),
        ("tool", "ok"),
        ("assistant", "confermato"),
        ("assistant", ALERT),
    ]
    # La coppia tool_calls/tool del turno è rimasta adiacente.
    assert session.messages[1]["tool_calls"][0]["id"] == "c1"
    assert session.messages[2]["tool_call_id"] == "c1"


async def test_a_monitor_alert_reaches_the_unified_session_end_to_end(
    loop_factory,
) -> None:
    """Il giro completo: il tool ``message`` di un turno silenzioso marca la
    consegna, il deliverer la inoltra all'hook del loop, la riga finisce in
    ``unified:default`` — non nella sessione del monitor che l'ha prodotta."""
    loop = loop_factory()
    published: list[OutboundMessage] = []

    class _Bus:
        async def publish_outbound(self, msg: OutboundMessage) -> None:
            published.append(msg)

    deliverer = ChannelDeliverer(
        bus=_Bus(),
        session_manager=loop.sessions,
        record_hook=lambda: loop.record_channel_delivery,
    )

    from jenny.agent.tools.context import RequestContext
    from jenny.agent.tools.message import MessageTool
    from jenny.session.turn_visibility import silent_turn_metadata

    tool = MessageTool(send_callback=deliverer.deliver)
    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="default",
            session_key="cron:rain-check",
            metadata=silent_turn_metadata(),
        )
    )
    tool.start_turn()

    result = await tool.execute(content=ALERT)

    assert "Message sent" in result
    # L'avviso, e dietro il ``turn_end`` che chiude il turno WebUI che ha
    # aperto (v. ``ChannelDeliverer._close_webui_turn``): il turno silenzioso
    # che l'ha prodotto non ne emette nessuno.
    assert [m.content for m in published] == [ALERT, ""]
    assert published[1].metadata.get("_turn_end") is True
    unified = loop.sessions.get_or_create(UNIFIED_SESSION_KEY).messages
    assert [m["content"] for m in unified] == [ALERT]
    assert loop.sessions.get_or_create("cron:rain-check").messages == []
