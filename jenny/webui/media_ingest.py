"""Ingestione di immagini remote nel media store locale.

Scarica un'immagine referenziata dall'agente (URL http/https) dentro
``<workspace>/.jenny/media/remote/`` così che possa essere servita dal gateway
via URL firmato ``/api/media/...`` invece di essere hotlinkata dall'host remoto.

Robustezza (vedi piano): validazione SSRF pre-fetch e su ogni hop di redirect,
cap di dimensione in streaming, validazione del tipo coi magic byte (mai il
Content-Type del server), dedup deterministico per URL e budget LRU sul solo
sottodir ``remote``. Modulo fidato di prima parte: importa ``httpx``
direttamente come i provider e ``apps/http.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from jenny.security.fetch import BROWSER_USER_AGENT, open_validated_stream, read_capped
from jenny.security.network import validate_url_target
from jenny.utils.helpers import detect_image_mime, ensure_dir
from jenny.utils.path import atomic_write

MediaDirProvider = Callable[[str | None], Path]

# Limiti operativi.
TIMEOUT_S = 15.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # allineato a media_decode.DEFAULT_MAX_BYTES
REMOTE_MEDIA_BUDGET_BYTES = 200 * 1024 * 1024  # cap LRU del sottodir remote/
_INGEST_CHANNEL = "remote"

# Solo i tipi raster che sappiamo riconoscere dai magic byte (detect_image_mime).
# Niente SVG: non è sniffabile e da host remoto è un vettore XSS.
_MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

_BROWSER_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "image/avif,image/webp,image/png,image/*;q=0.8,*/*;q=0.5",
}


def _url_stem(url: str) -> str:
    """Nome deterministico (senza estensione) derivato dall'URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _find_existing(remote_dir: Path, stem: str) -> Path | None:
    """Ritorna un file già ingerito per questo stem, se presente e non vuoto."""
    try:
        for candidate in remote_dir.glob(f"{stem}.*"):
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    except OSError:
        return None
    return None


async def _fetch_with_redirects(
    client: httpx.AsyncClient, url: str, *, logger: Any
) -> bytes | None:
    """Scarica ``url`` seguendo i redirect a mano, validando ogni hop (SSRF).

    Qualunque rifiuto — hop bloccato, redirect senza ``Location``, stato
    diverso da 200, troppi salti, immagine troppo grande — diventa una riga di
    log e ``None``: un'immagine remota che non arriva non è un errore da
    mostrare. I guasti di rete di httpx risalgono al chiamante, come prima.
    """
    try:
        async with open_validated_stream(
            client, url, validate=validate_url_target, headers=_BROWSER_HEADERS,
        ) as (resp, final_url):
            return await read_capped(
                resp, MAX_IMAGE_BYTES, f"{final_url} exceeds {MAX_IMAGE_BYTES} bytes",
            )
    except ValueError as exc:
        logger.warning("media ingest: {} not fetched: {}", url, exc)
        return None


def _enforce_remote_budget(remote_dir: Path, *, logger: Any) -> None:
    """Evince i file più vecchi (per mtime) finché il sottodir rientra nel cap."""
    try:
        files = [
            p
            for p in remote_dir.iterdir()
            if p.is_file() and not p.name.endswith(".tmp")
        ]
    except OSError:
        return

    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    total = sum(_size(f) for f in files)
    if total <= REMOTE_MEDIA_BUDGET_BYTES:
        return
    files.sort(key=lambda f: f.stat().st_mtime if f.exists() else 0.0)
    for f in files:
        if total <= REMOTE_MEDIA_BUDGET_BYTES:
            break
        size = _size(f)
        try:
            f.unlink()
        except OSError:
            continue
        total -= size
        logger.debug("media ingest: evicted {} ({} bytes) to enforce budget", f.name, size)


async def ingest_remote_image(
    url: str,
    *,
    media_dir: MediaDirProvider,
    logger: Any,
    client: httpx.AsyncClient | None = None,
) -> Path | None:
    """Scarica un'immagine remota nel media store locale.

    Ritorna il ``Path`` assoluto del file locale, o ``None`` se l'URL non è
    ingeribile (SSRF-bloccato, non-immagine, troppo grande, errore di rete).
    Idempotente: un URL già ingerito ritorna il file esistente senza rete.

    ``client`` è iniettabile per i test (``httpx.MockTransport``).
    """
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return None

    remote_dir = ensure_dir(media_dir(_INGEST_CHANNEL))
    stem = _url_stem(url)

    existing = _find_existing(remote_dir, stem)
    if existing is not None:
        return existing

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
    try:
        data = await _fetch_with_redirects(client, url, logger=logger)
    except httpx.HTTPError as exc:
        logger.warning("media ingest: fetch failed {}: {}", url, exc)
        return None
    finally:
        if owns_client:
            await client.aclose()

    if not data:
        return None

    mime = detect_image_mime(data)
    if mime is None or mime not in _MIME_EXT:
        logger.warning("media ingest: {} is not a recognized raster image", url)
        return None

    target = remote_dir / f"{stem}{_MIME_EXT[mime]}"
    try:
        atomic_write(target, data)
    except OSError as exc:
        logger.warning("media ingest: failed to persist {}: {}", url, exc)
        return None

    _enforce_remote_budget(remote_dir, logger=logger)
    return target
