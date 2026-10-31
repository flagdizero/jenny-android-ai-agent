"""Parser puri del canale WebSocket (estratti da websocket.py).

Funzioni pure di parsing/validazione degli input in ingresso: payload di testo,
envelope JSON, chat-id, MIME di data-URL, rilevamento upgrade WS. Modulo leaf:
nessuno stato, nessun import verso il canale.
"""

from __future__ import annotations

import json
import re
from typing import Any

from websockets.http11 import Request as WsRequest


def _parse_envelope(raw: str) -> dict[str, Any] | None:
    """Return a typed envelope dict if the frame is a new-style JSON envelope, else None.

    A frame qualifies when it parses as a JSON object with a string ``type`` field.
    Anything else (plain text, ``{"content": ...}`` without ``type``) returns None,
    and the channel answers it with an ``untyped_frame`` error.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    t = data.get("type")
    if not isinstance(t, str):
        return None
    return data


# Per-message media limits. The server-side guard is a touch looser than the
# client's ``Worker`` normalization target (6 MB) — tolerate client slop, but
# still cap total ingress at ``_MAX_IMAGES_PER_MESSAGE * _MAX_IMAGE_BYTES``
# which fits comfortably inside ``max_message_bytes``.
_MAX_IMAGES_PER_MESSAGE = 4
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_VIDEOS_PER_MESSAGE = 1
_MAX_VIDEO_BYTES = 20 * 1024 * 1024
# Allegati generici (documenti, archivi, qualsiasi file): salvati in
# ``uploads/`` e referenziati per path, letti on-demand dall'agente. Serviti
# dalla WebUI come ``application/octet-stream`` (download, mai eseguiti inline),
# quindi non c'è vincolo di whitelist MIME sull'ingest.
_MAX_FILES_PER_MESSAGE = 4
_MAX_FILE_BYTES = 20 * 1024 * 1024

# Image MIME whitelist — solo questi tipi vengono trattati come immagini
# (blocco vision + anteprima inline). SVG NON è incluso: eventuali SVG allegati
# ricadono nel bucket "file" generico (mai renderizzati inline come immagine).
_IMAGE_MIME_ALLOWED: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
})

_VIDEO_MIME_ALLOWED: frozenset[str] = frozenset({
    "video/mp4",
    "video/webm",
    "video/quicktime",
})

_DATA_URL_MIME_RE = re.compile(r"^data:([^;,]+)(?:;[^,]*)*;base64,", re.DOTALL)


def _extract_data_url_mime(url: str) -> str | None:
    """Return the MIME type of a ``data:<mime>;base64,...`` URL, else ``None``."""
    if not isinstance(url, str):
        return None
    m = _DATA_URL_MIME_RE.match(url)
    if not m:
        return None
    return m.group(1).strip().lower() or None


_IMAGE_NAME_RE = re.compile(r"\.(png|jpe?g|webp|gif)$", re.IGNORECASE)


def _name_looks_like_image(name: Any) -> bool:
    """Estensione-immagine nel nome file (fallback quando il MIME è assente)."""
    return isinstance(name, str) and bool(_IMAGE_NAME_RE.search(name))


def classify_media_item(item: Any) -> str:
    """Classifica un allegato come ``"video"`` | ``"image"`` | ``"file"``.

    Preferisce il MIME del data URL; se assente o generico
    (``application/octet-stream``) ripiega sull'estensione del nome. Le catture
    da fotocamera Android arrivano spesso senza MIME: senza questo fallback il
    conteggio per-tipo del server divergerebbe da quello del client, con rifiuti
    spuri sui cap (``too_many_files`` vs ``too_many_images``).
    """
    if not isinstance(item, dict):
        return "file"
    mime = _extract_data_url_mime(item.get("data_url", ""))
    if mime in _VIDEO_MIME_ALLOWED:
        return "video"
    if mime in _IMAGE_MIME_ALLOWED:
        return "image"
    if mime in (None, "application/octet-stream") and _name_looks_like_image(item.get("name")):
        return "image"
    return "file"


def _is_websocket_upgrade(request: WsRequest) -> bool:
    """Detect an actual WS upgrade; plain HTTP GETs to the same path should fall through."""
    upgrade = request.headers.get("Upgrade") or request.headers.get("upgrade")
    connection = request.headers.get("Connection") or request.headers.get("connection")
    if not upgrade or "websocket" not in upgrade.lower():
        return False
    if not connection or "upgrade" not in connection.lower():
        return False
    return True

