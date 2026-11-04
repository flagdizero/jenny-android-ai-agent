"""Test degli helper media firmati della WebUI (jenny/webui/media_api.py).

Copre firma/verifica HMAC dei path media, lo staging di file fuori dalla
media root, l'inferenza del "kind" per gli allegati, il mapping degli
allegati firmati e la risposta HTTP di ``serve_signed_media`` (incluse le
byte-range e i tentativi di manomissione/traversal).
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from websockets.http11 import Headers
from websockets.http11 import Request as WsRequest

from jenny.webui.media_api import (
    b64url_decode,
    b64url_encode,
    media_attachment_kind,
    serve_signed_media,
    sign_media_path,
    sign_or_stage_media_path,
    signed_media_attachments,
)

_SECRET = b"unit-test-media-secret"


@pytest.fixture()
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    return root


def _media_dir(media_root: Path):
    def _provider(channel: str | None = None) -> Path:
        target = media_root if channel is None else media_root / channel
        target.mkdir(parents=True, exist_ok=True)
        return target

    return _provider


def _sign(rel: str, secret: bytes = _SECRET) -> tuple[str, str]:
    """Firma manuale di un path relativo, per costruire URL anche manomessi."""
    payload = b64url_encode(rel.encode("utf-8"))
    mac = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()[:16]
    return b64url_encode(mac), payload


def _split_signed_url(url: str) -> tuple[str, str]:
    _, _, _, sig, payload = url.split("/")
    return sig, payload


# ---------------------------------------------------------------------------
# b64url roundtrip
# ---------------------------------------------------------------------------


def test_b64url_roundtrip() -> None:
    data = b"\x00\x01ciao\xff"
    encoded = b64url_encode(data)
    assert "=" not in encoded
    assert b64url_decode(encoded) == data


# ---------------------------------------------------------------------------
# sign_media_path
# ---------------------------------------------------------------------------


def test_sign_media_path_inside_root_returns_signed_url(media_root: Path) -> None:
    target = media_root / "img.png"
    target.write_bytes(b"fake-png")

    url = sign_media_path(target, secret=_SECRET, media_dir=_media_dir(media_root))

    assert url is not None
    assert url.startswith("/api/media/")
    sig, payload = _split_signed_url(url)
    assert b64url_decode(payload).decode("utf-8") == "img.png"
    expected_mac = hmac.new(_SECRET, payload.encode("ascii"), hashlib.sha256).digest()[:16]
    assert b64url_decode(sig) == expected_mac


def test_sign_media_path_outside_root_returns_none(tmp_path: Path, media_root: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")

    url = sign_media_path(outside, secret=_SECRET, media_dir=_media_dir(media_root))

    assert url is None


# ---------------------------------------------------------------------------
# sign_or_stage_media_path
# ---------------------------------------------------------------------------


def test_sign_or_stage_signs_directly_when_already_in_media_root(media_root: Path) -> None:
    target = media_root / "img.png"
    target.write_bytes(b"fake-png")

    result = sign_or_stage_media_path(target, secret=_SECRET, media_dir=_media_dir(media_root))

    assert result is not None
    assert result["name"] == "img.png"
    assert result["url"] == sign_media_path(target, secret=_SECRET, media_dir=_media_dir(media_root))


def test_sign_or_stage_copies_outside_file_into_websocket_channel(
    tmp_path: Path, media_root: Path
) -> None:
    outside = tmp_path / "attachment.jpg"
    outside.write_bytes(b"jpeg-bytes")

    result = sign_or_stage_media_path(outside, secret=_SECRET, media_dir=_media_dir(media_root))

    assert result is not None
    assert result["name"] == "attachment.jpg"
    staged_dir = media_root / "websocket"
    staged_files = list(staged_dir.glob("*-attachment.jpg"))
    assert len(staged_files) == 1
    assert staged_files[0].read_bytes() == b"jpeg-bytes"


def test_sign_or_stage_copies_a_file_once_across_renders(
    tmp_path: Path, media_root: Path
) -> None:
    """Ogni render della chat ricopiava l'allegato, disco senza limite.

    La copia ha un nome stabile per percorso + mtime + dimensione: un secondo
    render trova la copia e firma lo stesso URL.
    """
    outside = tmp_path / "attachment.jpg"
    outside.write_bytes(b"jpeg-bytes")
    provider = _media_dir(media_root)

    first = sign_or_stage_media_path(outside, secret=_SECRET, media_dir=provider)
    for _ in range(5):
        again = sign_or_stage_media_path(outside, secret=_SECRET, media_dir=provider)
        assert again == first

    assert len(list((media_root / "websocket").iterdir())) == 1


def test_sign_or_stage_restages_a_file_that_changed(tmp_path: Path, media_root: Path) -> None:
    import os

    outside = tmp_path / "attachment.jpg"
    outside.write_bytes(b"jpeg-bytes")
    provider = _media_dir(media_root)
    first = sign_or_stage_media_path(outside, secret=_SECRET, media_dir=provider)

    outside.write_bytes(b"other-jpeg-bytes, longer")
    st = outside.stat()
    os.utime(outside, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    second = sign_or_stage_media_path(outside, secret=_SECRET, media_dir=provider)

    assert second is not None and first is not None
    assert second["url"] != first["url"]
    staged = sorted(p.read_bytes() for p in (media_root / "websocket").iterdir())
    assert b"other-jpeg-bytes, longer" in staged


def test_sign_or_stage_keeps_the_staging_dir_within_its_budget(
    tmp_path: Path, media_root: Path, monkeypatch
) -> None:
    """Come ``remote/``: un budget LRU sulla cartella delle copie."""
    import os
    import time

    monkeypatch.setattr("jenny.webui.media_api.STAGED_MEDIA_BUDGET_BYTES", 250)
    provider = _media_dir(media_root)
    staged_dir = media_root / "websocket"
    results = []
    for i in range(5):
        src = tmp_path / f"f{i}.png"
        src.write_bytes(bytes([i]) * 100)
        results.append(sign_or_stage_media_path(src, secret=_SECRET, media_dir=provider))
        # mtime crescenti e distinti anche su filesystem a grana grossa.
        for staged in staged_dir.iterdir():
            if staged.name.endswith(f"-f{i}.png"):
                t = time.time() - 100 + i
                os.utime(staged, (t, t))

    assert all(r is not None for r in results)
    names = sorted(p.name.rsplit("-", 1)[-1] for p in staged_dir.iterdir())
    total = sum(p.stat().st_size for p in staged_dir.iterdir())
    assert total <= 250
    # Le più vecchie se ne vanno, l'ultima copiata resta sempre.
    assert names == ["f3.png", "f4.png"]


def test_sign_or_stage_never_evicts_the_copy_it_just_made(
    tmp_path: Path, media_root: Path, monkeypatch
) -> None:
    monkeypatch.setattr("jenny.webui.media_api.STAGED_MEDIA_BUDGET_BYTES", 10)
    src = tmp_path / "big.png"
    src.write_bytes(b"x" * 100)

    result = sign_or_stage_media_path(src, secret=_SECRET, media_dir=_media_dir(media_root))

    assert result is not None
    assert [p.name.rsplit("-", 1)[-1] for p in (media_root / "websocket").iterdir()] == [
        "big.png"
    ]


def test_sign_or_stage_returns_none_for_missing_file(tmp_path: Path, media_root: Path) -> None:
    missing = tmp_path / "nope.png"

    result = sign_or_stage_media_path(missing, secret=_SECRET, media_dir=_media_dir(media_root))

    assert result is None


def test_sign_or_stage_returns_none_and_logs_on_copy_failure(
    tmp_path: Path, media_root: Path, monkeypatch
) -> None:
    outside = tmp_path / "attachment.jpg"
    outside.write_bytes(b"jpeg-bytes")
    logger = MagicMock()

    def boom(*args, **kwargs):
        raise OSError("disco pieno")

    monkeypatch.setattr("jenny.utils.path.shutil.copyfile", boom)

    result = sign_or_stage_media_path(
        outside, secret=_SECRET, media_dir=_media_dir(media_root), logger=logger
    )

    assert result is None
    logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# media_attachment_kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("clip.mp4", "video"),
        ("photo.png", "image"),
        ("photo.jpg", "image"),
        ("document.pdf", "file"),
        ("noext", "file"),
    ],
)
def test_media_attachment_kind(name: str, expected: str) -> None:
    assert media_attachment_kind(name) == expected


# ---------------------------------------------------------------------------
# signed_media_attachments
# ---------------------------------------------------------------------------


def test_signed_media_attachments_maps_and_skips_unsignable(tmp_path: Path) -> None:
    def sign_path(path: Path):
        if path.name == "unsignable.png":
            return None
        if path.name == "no-url.png":
            return {"name": path.name}
        return {"url": f"/api/media/x/{path.name}", "name": path.name}

    out = signed_media_attachments(
        ["a/video.mp4", "b/unsignable.png", "c/no-url.png", "d/photo.png"],
        sign_path=sign_path,
    )

    assert [a["name"] for a in out] == ["video.mp4", "photo.png"]
    assert out[0]["kind"] == "video"
    assert out[1]["kind"] == "image"


# ---------------------------------------------------------------------------
# serve_signed_media
# ---------------------------------------------------------------------------


def test_serve_signed_media_invalid_signature_base64_returns_401(media_root: Path) -> None:
    response = serve_signed_media(
        "not-valid-base64!!!", "payload", secret=_SECRET, media_dir=_media_dir(media_root)
    )
    assert response.status_code == 401


def test_serve_signed_media_wrong_signature_returns_401(media_root: Path) -> None:
    _, payload = _sign("img.png")
    wrong_sig = b64url_encode(b"0" * 16)
    response = serve_signed_media(
        wrong_sig, payload, secret=_SECRET, media_dir=_media_dir(media_root)
    )
    assert response.status_code == 401


def test_serve_signed_media_invalid_payload_encoding_returns_400(media_root: Path) -> None:
    # Payload il cui decode base64 riesce ma produce bytes non UTF-8 validi.
    payload = b64url_encode(b"\xff\xfe")
    mac = hmac.new(_SECRET, payload.encode("ascii"), hashlib.sha256).digest()[:16]
    sig = b64url_encode(mac)

    response = serve_signed_media(sig, payload, secret=_SECRET, media_dir=_media_dir(media_root))

    assert response.status_code == 400


def test_serve_signed_media_rejects_path_escaping_media_root(media_root: Path) -> None:
    sig, payload = _sign("../outside.png")

    response = serve_signed_media(sig, payload, secret=_SECRET, media_dir=_media_dir(media_root))

    assert response.status_code == 404


def test_serve_signed_media_missing_file_returns_404(media_root: Path) -> None:
    sig, payload = _sign("missing.png")

    response = serve_signed_media(sig, payload, secret=_SECRET, media_dir=_media_dir(media_root))

    assert response.status_code == 404


def test_serve_signed_media_happy_path_returns_full_body(media_root: Path) -> None:
    target = media_root / "img.png"
    target.write_bytes(b"0123456789")
    sig, payload = _sign("img.png")

    response = serve_signed_media(sig, payload, secret=_SECRET, media_dir=_media_dir(media_root))

    assert response.status_code == 200
    assert response.body == b"0123456789"
    assert response.headers["Content-Type"] == "image/png"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_serve_signed_media_disallowed_mime_degrades_to_octet_stream(media_root: Path) -> None:
    target = media_root / "notes.txt"
    target.write_bytes(b"testo semplice")
    sig, payload = _sign("notes.txt")

    response = serve_signed_media(sig, payload, secret=_SECRET, media_dir=_media_dir(media_root))

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/octet-stream"


def test_serve_signed_media_svg_gets_extra_csp_header(media_root: Path) -> None:
    target = media_root / "icon.svg"
    target.write_bytes(b"<svg></svg>")
    sig, payload = _sign("icon.svg")

    response = serve_signed_media(sig, payload, secret=_SECRET, media_dir=_media_dir(media_root))

    assert response.status_code == 200
    # Il valore esatto, non «contiene sandbox»: un ``script-src *`` o un
    # ``sandbox allow-scripts`` aggiunti passerebbero un controllo a sottostringa.
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    )
    assert response.headers.get_all("Content-Security-Policy") == [
        response.headers["Content-Security-Policy"]
    ]


def _range_request(range_value: str) -> WsRequest:
    return WsRequest(path="/api/media/x/y", headers=Headers([("Range", range_value)]))


def test_serve_signed_media_valid_range_returns_206(media_root: Path) -> None:
    target = media_root / "img.png"
    target.write_bytes(b"0123456789")
    sig, payload = _sign("img.png")

    response = serve_signed_media(
        sig,
        payload,
        secret=_SECRET,
        request=_range_request("bytes=2-4"),
        media_dir=_media_dir(media_root),
    )

    assert response.status_code == 206
    assert response.body == b"234"
    assert response.headers["Content-Range"] == "bytes 2-4/10"


def test_serve_signed_media_suffix_range_returns_tail(media_root: Path) -> None:
    target = media_root / "img.png"
    target.write_bytes(b"0123456789")
    sig, payload = _sign("img.png")

    response = serve_signed_media(
        sig,
        payload,
        secret=_SECRET,
        request=_range_request("bytes=-3"),
        media_dir=_media_dir(media_root),
    )

    assert response.status_code == 206
    assert response.body == b"789"


def test_serve_signed_media_invalid_range_returns_416(media_root: Path) -> None:
    target = media_root / "img.png"
    target.write_bytes(b"0123456789")
    sig, payload = _sign("img.png")

    response = serve_signed_media(
        sig,
        payload,
        secret=_SECRET,
        request=_range_request("bytes=abc-def"),
        media_dir=_media_dir(media_root),
    )

    assert response.status_code == 416
