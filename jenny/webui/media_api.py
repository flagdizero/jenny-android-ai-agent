"""Signed media helpers for the WebUI HTTP surface."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import mimetypes
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

# Import dalla sede canonica: questo modulo è raggiungibile da
# ``channels.ws_sender`` a livello modulo, quindi importare da ``webui``
# creerebbe un ciclo webui→channels→webui.
from jenny.channels.http_utils import (
    case_insensitive_header as _case_insensitive_header,
)
from jenny.channels.http_utils import (
    http_error as _http_error,
)
from jenny.channels.http_utils import (
    http_response as _http_response,
)
from jenny.config.paths import get_media_dir
from jenny.security.workspace_policy import is_path_within
from jenny.utils.helpers import safe_filename
from jenny.utils.path import atomic_copy

MediaDirProvider = Callable[[str | None], Path]
SignedMediaPath = Callable[[Path], dict[str, str] | None]


def b64url_encode(data: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """Reverse of :func:`b64url_encode`; caller handles decode errors."""
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _default_media_dir(channel: str | None = None) -> Path:
    return get_media_dir(channel)


# Allowed MIME types we actually serve from the media endpoint. Anything
# outside this set is degraded to ``application/octet-stream`` so an
# attacker who somehow gets a signed URL for an unexpected file type can't
# trick the browser into sniffing executable content.
_MEDIA_ALLOWED_MIMES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/svg+xml",
    "video/mp4",
    "video/webm",
    "video/quicktime",
    # PDF: reso inline dal viewer del browser (fallback quando l'apertura
    # nativa Android non è disponibile, es. sviluppo su desktop).
    "application/pdf",
})
_SVG_MEDIA_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox",
    ),
)

_BYTE_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_single_byte_range(range_header: str, size: int) -> tuple[int, int]:
    """Parse a single HTTP byte range for signed media responses."""
    if size <= 0 or "," in range_header:
        raise ValueError("invalid byte range")
    m = _BYTE_RANGE_RE.fullmatch(range_header.strip())
    if m is None:
        raise ValueError("invalid byte range")
    start_text, end_text = m.groups()
    if not start_text and not end_text:
        raise ValueError("invalid byte range")
    if not start_text:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("invalid byte range")
        start = max(size - suffix_length, 0)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        if start >= size or start > end:
            raise ValueError("invalid byte range")
        end = min(end, size - 1)
    return start, end


def sign_media_path(
    abs_path: Path,
    *,
    secret: bytes,
    media_dir: MediaDirProvider = _default_media_dir,
) -> str | None:
    """Return a signed ``/api/media/<sig>/<payload>`` URL for a media-root path."""
    try:
        media_root = media_dir(None).resolve()
        rel = abs_path.resolve().relative_to(media_root)
    except (OSError, RuntimeError, ValueError):
        # ``RuntimeError``: un loop di symlink, su Python 3.11.
        return None
    payload = b64url_encode(rel.as_posix().encode("utf-8"))
    mac = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()[:16]
    return f"/api/media/{b64url_encode(mac)}/{payload}"


# Le copie dei file fuori dalla media root finiscono qui, e **solo** loro: la
# cartella si può potare senza toccare altro. Budget LRU come ``remote/``
# (``media_ingest.REMOTE_MEDIA_BUDGET_BYTES``): una copia potata si rifà al
# render successivo, finché il file d'origine esiste.
_STAGE_CHANNEL = "websocket"
STAGED_MEDIA_BUDGET_BYTES = 200 * 1024 * 1024


def _stage_key(path: Path, size: int, mtime_ns: int) -> str:
    """Nome stabile della copia: stesso file, stessa versione → stessa copia."""
    ident = f"{path}|{mtime_ns}|{size}"
    return hashlib.sha256(ident.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _enforce_staged_budget(staged_dir: Path, *, keep: Path, logger: Any | None) -> None:
    """Toglie le copie usate meno di recente finché la cartella sta nel budget.

    ``keep`` (la copia appena fatta) non si tocca mai, anche se da sola sfora:
    il render che l'ha chiesta deve poterla servire.
    """
    entries: list[tuple[float, int, Path]] = []
    try:
        for f in staged_dir.iterdir():
            if f.name.endswith(".tmp") or not f.is_file():
                continue
            st = f.stat()
            entries.append((st.st_mtime, st.st_size, f))
    except OSError:
        return
    total = sum(size for _, size, _ in entries)
    if total <= STAGED_MEDIA_BUDGET_BYTES:
        return
    for _, size, f in sorted(entries, key=lambda e: e[0]):
        if total <= STAGED_MEDIA_BUDGET_BYTES:
            break
        if f == keep:
            continue
        try:
            f.unlink()
        except OSError:
            continue
        total -= size
        if logger is not None:
            logger.debug("staged media: evicted {} ({} bytes) to enforce budget", f.name, size)


def sign_or_stage_media_path(
    path: Path,
    *,
    secret: bytes,
    media_dir: MediaDirProvider = _default_media_dir,
    logger: Any | None = None,
) -> dict[str, str] | None:
    """Sign an existing media-root path, or stage an arbitrary file before signing.

    La copia si fa una volta per versione del file (chiave: percorso, mtime,
    dimensione) e si riusa ai render successivi: prima ogni ridisegno della
    chat ne aggiungeva una, e la cartella cresceva senza limite.
    """
    signed = sign_media_path(path, secret=secret, media_dir=media_dir)
    if signed is not None:
        return {"url": signed, "name": path.name}
    try:
        if not path.is_file():
            return None
        st = path.stat()
        target_dir = media_dir(_STAGE_CHANNEL)
        safe_name = safe_filename(path.name) or "attachment"
        key = _stage_key(path.resolve(), st.st_size, st.st_mtime_ns)
        staged = target_dir / f"{key}-{safe_name}"
        if staged.is_file() and staged.stat().st_size == st.st_size:
            # Riusata: è «usata di recente» per il budget LRU.
            os.utime(staged)
        else:
            # Una copia a metà (disco pieno, processo ucciso) non deve mai
            # sembrare quella buona; il temporaneo il budget lo salta.
            atomic_copy(path, staged)
            _enforce_staged_budget(target_dir, keep=staged, logger=logger)
    except (OSError, RuntimeError) as exc:
        # ``RuntimeError``: un loop di symlink in ``resolve``, su Python 3.11.
        if logger is not None:
            logger.warning("failed to stage outbound media {}: {}", path, exc)
        return None
    signed = sign_media_path(staged, secret=secret, media_dir=media_dir)
    if signed is None:
        return None
    return {"url": signed, "name": path.name}


def media_attachment_kind(name: str) -> str:
    """Infer the WebUI media attachment kind from a filename."""
    mime, _ = mimetypes.guess_type(name)
    if mime and mime.startswith("video/"):
        return "video"
    if mime and mime.startswith("image/"):
        return "image"
    return "file"


def signed_media_attachments(
    paths: list[str],
    *,
    sign_path: SignedMediaPath,
) -> list[dict[str, Any]]:
    """Map persisted media paths to WebUI attachment dicts with fresh signed URLs."""
    out: list[dict[str, Any]] = []
    for pstr in paths:
        path = Path(pstr)
        att = sign_path(path)
        if att is None:
            continue
        url = att.get("url")
        if not url:
            continue
        name = att.get("name") or path.name
        out.append(
            {
                "kind": media_attachment_kind(name),
                "url": url,
                "name": name,
                # Path locale originario: il client lo usa per l'apertura
                # nativa dei file non renderizzabili inline (es. PDF).
                "path": pstr,
            }
        )
    return out


def serve_signed_media(
    sig: str,
    payload: str,
    *,
    secret: bytes,
    request: WsRequest | None = None,
    media_dir: MediaDirProvider = _default_media_dir,
) -> Response:
    """Serve a signed media URL, including browser-friendly byte ranges."""
    try:
        provided_mac = b64url_decode(sig)
    except (ValueError, binascii.Error):
        return _http_error(401, "invalid signature")
    expected_mac = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(expected_mac, provided_mac):
        return _http_error(401, "invalid signature")
    try:
        rel_bytes = b64url_decode(payload)
        rel_str = rel_bytes.decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return _http_error(400, "invalid payload")
    try:
        media_root = media_dir(None).resolve()
        candidate = (media_root / rel_str).resolve()
    except (OSError, RuntimeError, ValueError):
        # ``RuntimeError``: un loop di symlink, su Python 3.11.
        return _http_error(404, "not found")
    if not is_path_within(candidate, media_root, path_resolved=True):
        return _http_error(404, "not found")
    if not candidate.is_file():
        return _http_error(404, "not found")

    mime, _ = mimetypes.guess_type(candidate.name)
    if mime not in _MEDIA_ALLOWED_MIMES:
        mime = "application/octet-stream"
    common_headers = [
        ("Accept-Ranges", "bytes"),
        ("Cache-Control", "private, max-age=31536000, immutable"),
        ("X-Content-Type-Options", "nosniff"),
    ]
    if mime == "image/svg+xml":
        common_headers.extend(_SVG_MEDIA_HEADERS)
    try:
        size = candidate.stat().st_size
    except OSError:
        return _http_error(500, "read error")

    range_header = _case_insensitive_header(request.headers, "Range") if request else ""
    if range_header:
        try:
            start, end = _parse_single_byte_range(range_header, size)
        except ValueError:
            return _http_response(
                b"range not satisfiable",
                status=416,
                extra_headers=[
                    ("Accept-Ranges", "bytes"),
                    ("Content-Range", f"bytes */{size}"),
                    ("X-Content-Type-Options", "nosniff"),
                ],
            )
        try:
            length = end - start + 1
            with candidate.open("rb") as fh:
                fh.seek(start)
                body = fh.read(length)
        except OSError:
            return _http_error(500, "read error")
        return _http_response(
            body,
            status=206,
            content_type=mime,
            extra_headers=[
                *common_headers,
                ("Content-Range", f"bytes {start}-{end}/{size}"),
            ],
        )

    try:
        body = candidate.read_bytes()
    except OSError:
        return _http_error(500, "read error")
    return _http_response(body, content_type=mime, extra_headers=common_headers)
