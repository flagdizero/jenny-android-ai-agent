"""Test dei parser puri del canale WebSocket (``channels/ws_parsing.py``).

Funzioni leaf senza stato: payload legacy, envelope tipizzato, MIME dei
data-URL (whitelist anti-XSS: niente SVG) e rilevamento dell'upgrade WS.
"""

from __future__ import annotations

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.channels.ws_parsing import (
    _extract_data_url_mime,
    _is_websocket_upgrade,
    _parse_envelope,
    classify_media_item,
)

# -- classify_media_item ------------------------------------------------------


def test_classify_by_mime() -> None:
    assert classify_media_item({"data_url": "data:image/png;base64,AAAA"}) == "image"
    assert classify_media_item({"data_url": "data:video/mp4;base64,AAAA"}) == "video"
    assert classify_media_item({"data_url": "data:application/pdf;base64,AAAA"}) == "file"


def test_classify_camera_capture_without_mime_falls_back_to_name() -> None:
    # Android camera captures often arrive with an empty / octet-stream MIME:
    # the filename extension must still route them to the image bucket.
    assert classify_media_item(
        {"data_url": "data:application/octet-stream;base64,AAAA", "name": "cap_123.jpg"}
    ) == "image"


def test_classify_octet_stream_without_image_name_is_file() -> None:
    assert classify_media_item(
        {"data_url": "data:application/octet-stream;base64,AAAA", "name": "backup.jbk"}
    ) == "file"
    assert classify_media_item({"data_url": "data:application/octet-stream;base64,AAAA"}) == "file"


def test_classify_non_dict_is_file() -> None:
    assert classify_media_item("nope") == "file"
    assert classify_media_item(None) == "file"

# -- _parse_envelope ----------------------------------------------------------------


def test_envelope_requires_string_type() -> None:
    assert _parse_envelope('{"type": "chat", "content": "x"}') == {
        "type": "chat",
        "content": "x",
    }
    assert _parse_envelope('{"type": 3}') is None
    assert _parse_envelope('{"content": "legacy senza type"}') is None


@pytest.mark.parametrize("raw", ["testo piatto", "", "{spezzato", "[1]", "42"])
def test_envelope_rejects_non_object_frames(raw: str) -> None:
    assert _parse_envelope(raw) is None


# -- _extract_data_url_mime ---------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("data:image/png;base64,AAAA", "image/png"),
        ("data:IMAGE/PNG;base64,AAAA", "image/png"),  # case-insensitive
        ("data:image/webp;name=x;base64,AAAA", "image/webp"),  # parametri extra
        ("data:image/png,AAAA", None),  # non base64
        ("data:;base64,AAAA", None),  # MIME vuoto
        ("https://example.com/img.png", None),
        ("", None),
        (None, None),  # non-stringa tollerata
    ],
)
def test_extract_data_url_mime(url, expected) -> None:
    assert _extract_data_url_mime(url) == expected


# -- _is_websocket_upgrade ----------------------------------------------------------


def _request(headers: dict[str, str]) -> WsRequest:
    h = Headers()
    for key, value in headers.items():
        h[key] = value
    return WsRequest(path="/", headers=h)


def test_upgrade_detected_with_canonical_headers() -> None:
    assert _is_websocket_upgrade(
        _request({"Upgrade": "websocket", "Connection": "Upgrade"})
    )


def test_upgrade_detected_case_insensitive() -> None:
    assert _is_websocket_upgrade(
        _request({"upgrade": "WebSocket", "connection": "keep-alive, Upgrade"})
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},  # GET semplice
        {"Upgrade": "websocket"},  # manca Connection
        {"Connection": "Upgrade"},  # manca Upgrade
        {"Upgrade": "h2c", "Connection": "Upgrade"},  # upgrade diverso
        {"Upgrade": "websocket", "Connection": "close"},
    ],
)
def test_plain_http_requests_fall_through(headers: dict[str, str]) -> None:
    assert not _is_websocket_upgrade(_request(headers))
