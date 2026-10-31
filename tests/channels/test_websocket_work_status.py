"""Il frame WS ``work_status`` e il ``work_id`` sui messaggi di testo.

La stessa disciplina dello snapshot dei subagent (v.
``test_websocket_subagent_status.py``): un frame dedicato che non diventa mai una
bolla vuota e non tocca il transcript, solo per chi e' collegato a quella chat.
In piu' il resoconto di testo del lavoro porta il suo id, cosi' la home non lo
disegna due volte.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from jenny.bus.events import (
    OUTBOUND_META_WORK_ID,
    OUTBOUND_META_WORK_START,
    OUTBOUND_META_WORK_STATUS,
    OutboundMessage,
)
from tests.channels.test_websocket_subagent_status import _channel, _subscribed_conn

_PAYLOAD = {
    "id": "gardener-abc", "kind": "gardener", "state": "running", "manual": True,
    "phases": ["pages", "board"], "phase": "pages", "writes": 3, "retry": None,
    "started_at": 1.0, "updated_at": 2.0, "project": "orto",
}


def _msg(chat_id: str = "project:orto") -> OutboundMessage:
    return OutboundMessage(
        channel="websocket", chat_id=chat_id, content="",
        metadata={OUTBOUND_META_WORK_STATUS: dict(_PAYLOAD)},
    )


async def test_it_is_a_dedicated_frame_with_the_payload() -> None:
    channel = _channel()
    conn = _subscribed_conn(channel, "project:orto")

    assert await channel.send(_msg()) == []

    frame = json.loads(conn.send.await_args.args[0])
    assert frame["event"] == "work_status"
    assert frame["chat_id"] == "project:orto"
    assert frame["id"] == "gardener-abc"
    assert frame["writes"] == 3
    assert "text" not in frame


async def test_it_never_becomes_a_transcript_row() -> None:
    channel = _channel()
    _subscribed_conn(channel, "project:orto")

    await channel.send(_msg())

    channel._transcripts.prepare_and_append.assert_not_called()


async def test_it_reaches_only_the_chat_it_names() -> None:
    channel = _channel()
    elsewhere = _subscribed_conn(channel, "default")

    await channel.send(_msg("project:orto"))

    elsewhere.send.assert_not_awaited()
    channel._transcripts.prepare_and_append.assert_not_called()


async def test_the_text_report_carries_the_work_id() -> None:
    channel = _channel()
    conn = _subscribed_conn(channel, "project:orto")
    channel._media.localize_remote_media = AsyncMock(return_value=("Fatto.", []))
    channel._media.rewrite_local_markdown_images = MagicMock(return_value="Fatto.")

    await channel.send(OutboundMessage(
        channel="websocket", chat_id="project:orto", content="Fatto.",
        metadata={OUTBOUND_META_WORK_ID: "gardener-abc"},
    ))

    frame = json.loads(conn.send.await_args.args[0])
    assert frame["event"] == "message"
    assert frame["work_id"] == "gardener-abc"
    persisted = channel._transcripts.prepare_and_append.call_args.args[1]
    assert persisted["work_id"] == "gardener-abc"
    assert "work_start" not in persisted


async def test_the_announcement_says_it_is_one() -> None:
    channel = _channel()
    conn = _subscribed_conn(channel, "project:orto")
    channel._media.localize_remote_media = AsyncMock(return_value=("Gardening orto...", []))
    channel._media.rewrite_local_markdown_images = MagicMock(return_value="Gardening orto...")

    await channel.send(OutboundMessage(
        channel="websocket", chat_id="project:orto", content="Gardening orto...",
        metadata={OUTBOUND_META_WORK_ID: "gardener-abc", OUTBOUND_META_WORK_START: True},
    ))

    frame = json.loads(conn.send.await_args.args[0])
    assert frame["work_start"] is True
    persisted = channel._transcripts.prepare_and_append.call_args.args[1]
    assert persisted["work_start"] is True


async def test_a_turn_that_ended_in_an_error_says_so_on_the_wire_and_on_disk() -> None:
    """Il contratto con la WebUI: ``"error": {"kind": "<tipo>"}`` nel frame e nel
    transcript, solo ``kind``, e niente chiave se i metadata non la portano."""
    channel = _channel()
    conn = _subscribed_conn(channel, "default")
    text = "I can't reach the model."
    channel._media.localize_remote_media = AsyncMock(return_value=(text, []))
    channel._media.rewrite_local_markdown_images = MagicMock(return_value=text)

    await channel.send(OutboundMessage(
        channel="websocket", chat_id="default", content=text,
        metadata={"error": {"kind": "connection", "raw": "Error calling LLM: boom"}},
    ))

    frame = json.loads(conn.send.await_args.args[0])
    assert frame["event"] == "message"
    assert frame["error"] == {"kind": "connection"}
    persisted = channel._transcripts.prepare_and_append.call_args.args[1]
    assert persisted["error"] == {"kind": "connection"}

    await channel.send(OutboundMessage(channel="websocket", chat_id="default", content=text))
    assert "error" not in json.loads(conn.send.await_args.args[0])
