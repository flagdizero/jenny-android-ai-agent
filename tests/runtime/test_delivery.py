"""Test per jenny/runtime/delivery.py (``ChannelDeliverer``).

Copre: pubblicazione sul bus, mirroring nella sessione unificata quando
``record`` è attivo (via parametro o via metadata ``_record_channel_delivery``),
l'inoltro della registrazione all'hook dell'AgentLoop quando c'è (e il fallback
alla scrittura diretta quando non c'è), i branch che saltano il mirroring
(canale interno, contenuto vuoto, session manager senza i metodi richiesti) e la
propagazione degli errori del canale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from support.bus import FakeBus

from jenny.bus.events import INTERNAL_CHANNEL, OutboundMessage
from jenny.runtime.delivery import ChannelDeliverer
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.manager import SessionManager


def _deliverer(
    tmp_path: Path, *, bus: FakeBus | None = None
) -> tuple[ChannelDeliverer, FakeBus, SessionManager]:
    bus = bus or FakeBus()
    session_manager = SessionManager(tmp_path)
    return ChannelDeliverer(bus=bus, session_manager=session_manager), bus, session_manager


async def test_deliver_without_record_only_publishes(tmp_path: Path) -> None:
    deliverer, bus, sessions = _deliverer(tmp_path)
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao")

    await deliverer.deliver(msg)

    assert bus.outbound == [msg]
    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert session.messages == []


async def test_deliver_with_record_mirrors_into_unified_session(tmp_path: Path) -> None:
    deliverer, bus, sessions = _deliverer(tmp_path)
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao")

    await deliverer.deliver(msg, record=True)

    assert bus.outbound == [msg]
    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert len(session.messages) == 1
    recorded = session.messages[0]
    assert recorded["role"] == "assistant"
    assert recorded["content"] == "ciao"
    assert recorded["_channel_delivery"] is True
    assert "media" not in recorded


async def test_deliver_with_record_includes_media_copy(tmp_path: Path) -> None:
    deliverer, _bus, sessions = _deliverer(tmp_path)
    media = ["file:///a.png"]
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao", media=media)

    await deliverer.deliver(msg, record=True)

    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    recorded_media = session.messages[0]["media"]
    assert recorded_media == media
    assert recorded_media is not media  # copia, non lo stesso oggetto della lista originale


async def test_deliver_skips_recording_for_internal_channel(tmp_path: Path) -> None:
    deliverer, bus, sessions = _deliverer(tmp_path)
    msg = OutboundMessage(channel=INTERNAL_CHANNEL, chat_id="1", content="ciao")

    await deliverer.deliver(msg, record=True)

    assert bus.outbound == [msg]
    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert session.messages == []


async def test_deliver_skips_recording_for_blank_content(tmp_path: Path) -> None:
    deliverer, _bus, sessions = _deliverer(tmp_path)
    msg = OutboundMessage(channel="websocket", chat_id="1", content="   \n  ")

    await deliverer.deliver(msg, record=True)

    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert session.messages == []


async def test_deliver_reads_record_flag_from_metadata_and_strips_it(tmp_path: Path) -> None:
    deliverer, bus, sessions = _deliverer(tmp_path)
    msg = OutboundMessage(
        channel="websocket",
        chat_id="1",
        content="ciao",
        buttons=[["ok"]],
        metadata={"_record_channel_delivery": True, "other": "x"},
    )

    await deliverer.deliver(msg)

    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert len(session.messages) == 1
    published = bus.outbound[0]
    assert published is not msg  # ricostruito perché i metadata sono cambiati
    assert published.metadata == {"other": "x"}
    assert published.buttons == [["ok"]]


async def test_deliver_without_marker_metadata_keeps_original_message_object(
    tmp_path: Path,
) -> None:
    deliverer, bus, _sessions = _deliverer(tmp_path)
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao", metadata={"x": 1})

    await deliverer.deliver(msg, record=True)

    assert bus.outbound[0] is msg  # metadata invariati: nessuna ricostruzione


async def test_deliver_uses_explicit_session_key_override(tmp_path: Path) -> None:
    deliverer, _bus, sessions = _deliverer(tmp_path)
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao")

    await deliverer.deliver(msg, record=True, session_key="custom:key")

    custom_session = sessions.get_or_create("custom:key")
    assert len(custom_session.messages) == 1
    unified_session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert unified_session.messages == []


async def test_deliver_skips_recording_when_session_manager_lacks_hooks(tmp_path: Path) -> None:
    class _BareSessionManager:
        """Senza get_or_create/save: il duck-typing del modulo deve saltare il mirroring."""

    bus = FakeBus()
    deliverer = ChannelDeliverer(bus=bus, session_manager=_BareSessionManager())
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao")

    # Non deve sollevare nonostante manchino i metodi di sessione.
    await deliverer.deliver(msg, record=True)

    assert bus.outbound == [msg]


async def test_deliver_routes_recording_through_the_hook_when_present(tmp_path: Path) -> None:
    """Con l'agente vivo la scrittura passa da lui: è l'unico che possa
    serializzarla col lock di sessione di un turno in corso."""
    calls: list[dict[str, Any]] = []

    async def _hook(**kwargs: Any) -> None:
        calls.append(kwargs)

    bus = FakeBus()
    sessions = SessionManager(tmp_path)
    deliverer = ChannelDeliverer(
        bus=bus, session_manager=sessions, record_hook=lambda: _hook
    )
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao", media=["/a.png"])

    await deliverer.deliver(msg, record=True)

    assert calls == [
        {"session_key": UNIFIED_SESSION_KEY, "content": "ciao", "media": ["/a.png"]}
    ]
    # Nessuna scrittura diretta: l'hook è la sola via.
    assert sessions.get_or_create(UNIFIED_SESSION_KEY).messages == []
    assert bus.outbound == [msg]


async def test_deliver_falls_back_to_direct_write_when_hook_is_unset(tmp_path: Path) -> None:
    """Prima dell'onboarding l'agente non esiste ancora: il getter torna None e
    la registrazione resta quella diretta."""
    deliverer, _bus, sessions = _deliverer(tmp_path)
    deliverer._record_hook = lambda: None
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao")

    await deliverer.deliver(msg, record=True)

    assert len(sessions.get_or_create(UNIFIED_SESSION_KEY).messages) == 1


async def test_deliver_survives_a_failing_record_hook(tmp_path: Path) -> None:
    """Perdere la riga di cronologia è meno grave che perdere l'avviso."""

    async def _hook(**_kwargs: Any) -> None:
        raise RuntimeError("sessione illeggibile")

    bus = FakeBus()
    deliverer = ChannelDeliverer(
        bus=bus, session_manager=SessionManager(tmp_path), record_hook=lambda: _hook
    )
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao")

    await deliverer.deliver(msg, record=True)

    assert bus.outbound == [msg]


async def test_deliver_propagates_bus_errors(tmp_path: Path) -> None:
    deliverer, _bus, sessions = _deliverer(
        tmp_path, bus=FakeBus(raise_error=RuntimeError("canale rotto"))
    )
    msg = OutboundMessage(channel="websocket", chat_id="1", content="ciao")

    with pytest.raises(RuntimeError, match="canale rotto"):
        await deliverer.deliver(msg, record=True)

    # Il mirroring in sessione avviene PRIMA della publish: il messaggio resta
    # registrato anche se la consegna sul canale fallisce.
    session = sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert len(session.messages) == 1
