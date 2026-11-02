"""Tests for WS envelope media handling (client image upload path).

Exercises ``WebSocketChannel._dispatch_envelope`` for the ``message`` branch:
decoding base64 data URLs, rejecting malformed / oversized / non-whitelisted
payloads, preserving backward compatibility with media-less frames, and
forwarding saved paths to ``_handle_message``.
"""

from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from support.ws_channel import make_ws_channel

from jenny.channels.websocket import (
    WebSocketChannel,
    _extract_data_url_mime,
)


def _tiny_png_data_url() -> str:
    """A 1-pixel PNG prefixed as a data URL — just enough for magic-bytes sniffing."""
    # 1x1 transparent PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
        b"\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx"
        b"\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x00\x18\xdd\x8d\xb4\x00"
        b"\x00\x00\x00IEND\xaeB`\x82"
    )
    return f"data:image/png;base64,{base64.b64encode(png).decode()}"


def _data_url(mime: str, payload: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


def _make_channel() -> WebSocketChannel:
    channel = make_ws_channel()
    channel._handle_message = AsyncMock()  # type: ignore[method-assign]
    return channel


# -- Pure helpers --------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("data:image/png;base64,AAAA", "image/png"),
        ("data:image/jpeg;base64,AAAA", "image/jpeg"),
        ("data:audio/webm;codecs=opus;base64,AAAA", "audio/webm"),
        ("data:IMAGE/PNG;base64,AAAA", "image/png"),
        ("data:image/svg+xml;base64,AAAA", "image/svg+xml"),
        ("data:text/plain;base64,AAAA", "text/plain"),
        ("http://evil.example/x.png", None),
        ("data:image/png,AAAA", None),  # missing `;base64`
        ("", None),
        (None, None),
    ],
)
def test_extract_data_url_mime(url: Any, expected: str | None) -> None:
    assert _extract_data_url_mime(url) == expected


# -- max_message_bytes bump ----------------------------------------------------


def test_max_message_bytes_default_supports_multi_image_frame() -> None:
    """Default 36 MB must comfortably hold 4 × 6 MB base64-encoded images."""
    from jenny.channels.websocket import WebSocketConfig

    default = WebSocketConfig().max_message_bytes
    # 4 images × 6 MB × 1.37 base64 overhead ≈ 33 MB
    assert default >= 33 * 1024 * 1024
    # Upper bound 40 MB matches plan
    with pytest.raises(Exception):
        WebSocketConfig(max_message_bytes=41_943_040 + 1)


# -- _dispatch_envelope message branch + media --------------------------------


@pytest.mark.asyncio
async def test_message_without_media_backward_compatible() -> None:
    """Existing clients that don't send ``media`` keep working unchanged."""
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {"type": "message", "chat_id": "abc123", "content": "hello"}

    await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_awaited_once()
    call = channel._handle_message.call_args
    # chat_id in the envelope is ignored: everything routes to the unified chat.
    assert call.kwargs["chat_id"] == "default"
    assert call.kwargs["content"] == "hello"
    # When no media, we pass ``media=None`` so downstream treats it as absent.
    assert call.kwargs["media"] is None


@pytest.mark.asyncio
async def test_message_with_single_image_forwards_saved_path(tmp_path) -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "look at this",
        "media": [{"data_url": _tiny_png_data_url(), "name": "shot.png"}],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_awaited_once()
    paths = channel._handle_message.call_args.kwargs["media"]
    assert isinstance(paths, list) and len(paths) == 1
    saved = Path(paths[0])
    assert saved.exists()
    assert saved.suffix == ".png"
    assert saved.is_relative_to(tmp_path)


@pytest.mark.asyncio
async def test_message_with_multiple_images(tmp_path) -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "a couple",
        "media": [
            {"data_url": _tiny_png_data_url()},
            {"data_url": _tiny_png_data_url()},
            {"data_url": _tiny_png_data_url()},
        ],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    paths = channel._handle_message.call_args.kwargs["media"]
    assert len(paths) == 3
    # Saved filenames must be unique.
    assert len({Path(p).name for p in paths}) == 3


@pytest.mark.asyncio
async def test_image_only_message_allows_empty_text(tmp_path) -> None:
    """When media is attached, empty text is acceptable."""
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "",
        "media": [{"data_url": _tiny_png_data_url()}],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_awaited_once()
    # Error event NOT sent.
    mock_conn.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_rejected_when_more_than_four_images(tmp_path) -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "hi",
        "media": [{"data_url": _tiny_png_data_url()}] * 5,
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    mock_conn.send.assert_awaited_once()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["event"] == "error"
    assert err["detail"] == "image_rejected"
    assert err["reason"] == "too_many_images"


@pytest.mark.asyncio
async def test_message_rejected_on_oversize_payload(tmp_path) -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    oversized = b"x" * (9 * 1024 * 1024)  # > 8 MB WS limit
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "big",
        "media": [{"data_url": _data_url("image/png", oversized)}],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["detail"] == "image_rejected"
    assert err["reason"] == "size"


@pytest.mark.asyncio
async def test_message_with_document_is_accepted(tmp_path) -> None:
    """Non-image files (PDF, docs, …) are accepted and saved as generic
    attachments — the agent reads them on-demand by path reference."""
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "look at this report",
        "media": [{"data_url": _data_url("application/pdf", b"%PDF-1.4"), "name": "report.pdf"}],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_awaited_once()
    paths = channel._handle_message.call_args.kwargs["media"]
    assert isinstance(paths, list) and len(paths) == 1
    saved = Path(paths[0])
    assert saved.exists()
    assert saved.suffix == ".pdf"
    # No rejection error was sent.
    mock_conn.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_with_unknown_mime_keeps_extension_from_name(tmp_path) -> None:
    """A file sent as application/octet-stream keeps its extension from the
    original filename instead of collapsing to .bin."""
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "",
        "media": [
            {"data_url": _data_url("application/octet-stream", b"PK\x03\x04"), "name": "sheet.xlsx"}
        ],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_awaited_once()
    saved = Path(channel._handle_message.call_args.kwargs["media"][0])
    assert saved.suffix == ".xlsx"


@pytest.mark.asyncio
async def test_message_with_svg_is_accepted_as_generic_file(tmp_path) -> None:
    """SVG is not treated as an inline image (never rendered as such), but it is
    accepted as a generic downloadable file — served as octet-stream, so no XSS
    surface. It must NOT be rejected."""
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "svg",
        "media": [{"data_url": _data_url("image/svg+xml", b"<svg/>"), "name": "icon.svg"}],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_awaited_once()
    mock_conn.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_rejected_when_more_than_four_files(tmp_path) -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "many files",
        "media": [{"data_url": _data_url("application/pdf", b"%PDF-1.4")}] * 5,
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["detail"] == "image_rejected"
    assert err["reason"] == "too_many_files"


@pytest.mark.asyncio
async def test_message_rejected_on_malformed_data_url(tmp_path) -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "nope",
        "media": [{"data_url": "http://evil.example/image.png"}],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["reason"] == "decode"


@pytest.mark.asyncio
async def test_message_rejected_on_broken_base64(tmp_path) -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "nope",
        "media": [{"data_url": "data:image/png;base64,not-valid-base64!!!"}],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["reason"] == "decode"


@pytest.mark.asyncio
async def test_message_rejected_when_media_item_shape_wrong(tmp_path) -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "huh",
        # Not a dict — plain string at the top level.
        "media": ["data:image/png;base64,XXXX"],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["reason"] == "malformed"


@pytest.mark.asyncio
async def test_message_rejected_when_media_field_is_not_list() -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "huh",
        "media": "not-a-list",
    }

    await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["detail"] == "image_rejected"
    assert err["reason"] == "malformed"


@pytest.mark.asyncio
async def test_failed_media_does_not_partially_persist(tmp_path) -> None:
    """If the second image is invalid, the first must not be forwarded.

    Also: images already written in this call are cleaned up on failure, so
    a mixed-valid/invalid batch never leaves orphan files in the media dir.
    """
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "mixed",
        "media": [
            {"data_url": _tiny_png_data_url()},
            # Second item has a broken base64 payload → decode failure after
            # the first item was already written to disk.
            {"data_url": "data:image/png;base64,not-valid-base64!!!"},
        ],
    }

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["reason"] == "decode"
    # Partial-batch failures must not leak files to disk.
    leftover = [p for p in tmp_path.iterdir() if p.is_file()]
    assert leftover == [], f"orphan media after rejected batch: {leftover}"


@pytest.mark.asyncio
async def test_rejects_empty_text_without_media() -> None:
    """When no media is attached, whitespace-only content is still rejected
    (matches the existing behavior for backward compat)."""
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "   ",
    }

    await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["detail"] == "missing content"


@pytest.mark.asyncio
async def test_non_string_content_still_rejected() -> None:
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": 42,
    }

    await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_not_awaited()
    err = json.loads(mock_conn.send.call_args[0][0])
    assert err["detail"] == "missing content"


@pytest.mark.asyncio
async def test_media_decode_runs_off_event_loop_thread(tmp_path) -> None:
    """The synchronous base64 decode + disk write must run OFF the event loop.

    Decoding a 20 MB video on an Android CPU takes hundreds of ms; doing it
    inline in the async dispatch would freeze the entire gateway. The dispatch
    offloads ``_save_envelope_media`` via ``asyncio.to_thread``, so it must
    execute on a worker thread, never the main (event-loop) thread.
    """
    channel = _make_channel()
    mock_conn = AsyncMock()
    envelope = {
        "type": "message",
        "chat_id": "abc123",
        "content": "off-thread",
        "media": [{"data_url": _tiny_png_data_url()}],
    }

    observed: dict[str, Any] = {}
    real_decode = None
    from jenny.channels import websocket as ws_mod

    real_decode = ws_mod.save_base64_data_url

    def _spy(*args: Any, **kwargs: Any):
        observed["off_main_thread"] = (
            threading.current_thread() is not threading.main_thread()
        )
        observed["thread_name"] = threading.current_thread().name
        return real_decode(*args, **kwargs)

    with patch(
        "jenny.channels.websocket.get_uploads_dir", return_value=tmp_path
    ), patch("jenny.channels.websocket.save_base64_data_url", side_effect=_spy):
        await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_awaited_once()
    assert observed.get("off_main_thread") is True, (
        f"decode ran on {observed.get('thread_name')} (expected a worker thread)"
    )


@pytest.mark.asyncio
async def test_attach_returns_default_chat_id() -> None:
    """Every attach lands on the fixed unified chat_id 'default'."""
    channel = _make_channel()
    mock_conn = AsyncMock()

    envelope = {"type": "attach"}
    await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    calls = [json.loads(c[0][0]) for c in mock_conn.send.call_args_list]
    attached = next(c for c in calls if c["event"] == "attached")
    assert attached["chat_id"] == "default"


@pytest.mark.asyncio
async def test_unified_session_ignores_client_chat_id_on_attach() -> None:
    """Clients cannot escape the shared chat."""
    channel = _make_channel()
    mock_conn = AsyncMock()

    envelope = {"type": "attach", "chat_id": "some-other-id"}
    await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    attached = json.loads(mock_conn.send.call_args[0][0])
    assert attached["event"] == "attached"
    assert attached["chat_id"] == "default"


@pytest.mark.asyncio
async def test_unified_session_ignores_client_chat_id_on_message() -> None:
    """Messages are always routed to the shared default chat_id."""
    channel = _make_channel()
    mock_conn = AsyncMock()

    envelope = {"type": "message", "chat_id": "another-id", "content": "hello"}
    await channel._dispatch_envelope(mock_conn, "client-1", envelope)

    channel._handle_message.assert_awaited_once()
    assert channel._handle_message.await_args.kwargs["chat_id"] == "default"
