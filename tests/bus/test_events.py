"""Test diretti per jenny/bus/events.py: dataclass e costanti degli eventi.

InboundMessage/OutboundMessage sono ampiamente usati (e implicitamente
esercitati) in molti altri test, ma nessuno testa qui il *contratto* del
dataclass in sé (default, mutabilità, session_key override). Questo file
copre solo quello.
"""

from __future__ import annotations

from datetime import datetime

from jenny.bus.events import (
    INTERNAL_CHANNEL,
    OUTBOUND_META_AGENT_UI,
    InboundMessage,
    OutboundMessage,
)
from jenny.session.keys import UNIFIED_SESSION_KEY

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------


def test_internal_channel_constant_value() -> None:
    assert INTERNAL_CHANNEL == "internal"


def test_outbound_meta_agent_ui_constant_value() -> None:
    assert OUTBOUND_META_AGENT_UI == "_agent_ui"


# ---------------------------------------------------------------------------
# InboundMessage
# ---------------------------------------------------------------------------


def test_inbound_message_required_fields() -> None:
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="ciao")
    assert msg.channel == "websocket"
    assert msg.sender_id == "u1"
    assert msg.chat_id == "c1"
    assert msg.content == "ciao"


def test_inbound_message_default_media_and_metadata_are_empty() -> None:
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="ciao")
    assert msg.media == []
    assert msg.metadata == {}




def test_inbound_message_default_timestamp_is_datetime() -> None:
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="ciao")
    assert isinstance(msg.timestamp, datetime)


def test_inbound_message_without_an_override_is_the_one_conversation() -> None:
    """Senza override la sessione è quella dell'utente, non ``canale:chat``.

    Questo test diceva il contrario fino al 2026-08-21, e la forma che
    asseriva — ``websocket:room-42`` — non è una sessione: nessun file di
    sessione la porta. ``CronTool`` però la persisteva nei payload dei job, e
    ``bound_runner`` la usa come chiave del turno: un promemoria creato dalla
    WebUI finiva per girare in ``websocket:default``, un secondo file di
    sessione accanto alla conversazione a cui appartiene.
    """
    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="room-42", content="ciao")
    assert msg.session_key == UNIFIED_SESSION_KEY


def test_inbound_message_session_key_override_wins() -> None:
    msg = InboundMessage(
        channel="websocket",
        sender_id="u1",
        chat_id="room-42",
        content="ciao",
        session_key_override="unified:default",
    )
    assert msg.session_key == "unified:default"


def test_inbound_message_override_none_does_not_invent_an_internal_key() -> None:
    """Il canale **non** conia una chiave interna: quella la passa il chiamante.

    Ed è il verso giusto in cui sbagliare. Prima un messaggio su canale
    ``cron`` senza override si fabbricava ``cron:job-1``, cioè *diventava* una
    sessione interna per come era scritto il canale; oggi resta la
    conversazione dell'utente finché qualcuno non dice il contrario in modo
    esplicito. Chi ha bisogno di una sessione interna la nomina — ogni
    chiamante di produzione lo fa già (``session_key_override``, o il parametro
    ``session_key`` di ``process_direct``).
    """
    msg = InboundMessage(
        channel="cron",
        sender_id="system",
        chat_id="job-1",
        content="tick",
        session_key_override=None,
    )
    assert msg.session_key == UNIFIED_SESSION_KEY


def test_inbound_message_session_key_override_is_the_way_to_go_internal() -> None:
    msg = InboundMessage(
        channel="cron",
        sender_id="system",
        chat_id="job-1",
        content="tick",
        session_key_override="cron:job-1",
    )
    assert msg.session_key == "cron:job-1"


def test_internal_channel_is_a_channel_and_not_a_session() -> None:
    """``INTERNAL_CHANNEL`` dice da dove arriva il turno, non dove viene lavorato.

    Le due cose coincidevano per accidente della vecchia property, che dal
    canale ``internal`` ricavava la chiave ``internal:job-1``. Restano separate
    di proposito: il canale è il trasporto (nessuna consegna), la sessione la
    scelgono i chiamanti — ``process_direct`` ha ``internal:direct`` come
    default del suo parametro, che è il posto dove quel valore appartiene.
    """
    msg = InboundMessage(
        channel=INTERNAL_CHANNEL, sender_id="cron", chat_id="job-1", content="tick"
    )
    assert msg.session_key == UNIFIED_SESSION_KEY


# ---------------------------------------------------------------------------
# OutboundMessage
# ---------------------------------------------------------------------------


def test_outbound_message_required_fields_and_defaults() -> None:
    msg = OutboundMessage(channel="websocket", chat_id="c1", content="risposta")
    assert msg.media == []
    assert msg.metadata == {}
    assert msg.buttons == []


def test_outbound_message_default_factories_are_independent_per_instance() -> None:
    a = OutboundMessage(channel="websocket", chat_id="c1", content="a")
    b = OutboundMessage(channel="websocket", chat_id="c1", content="b")
    a.metadata["k"] = "v"
    a.buttons.append(["btn"])
    assert b.metadata == {}
    assert b.buttons == []


def test_outbound_message_metadata_carries_agent_ui_payload() -> None:
    msg = OutboundMessage(
        channel="websocket",
        chat_id="c1",
        content="",
        metadata={OUTBOUND_META_AGENT_UI: {"kind": "card", "title": "x"}},
    )
    assert msg.metadata[OUTBOUND_META_AGENT_UI] == {"kind": "card", "title": "x"}
