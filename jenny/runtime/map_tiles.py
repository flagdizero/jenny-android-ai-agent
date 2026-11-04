"""Le tessere della mappa nelle schede dei posti in chat.

La scheda (``shared/place-card.js``) chiede le sue tessere a
``/api/map/tile/{z}/{x}/{y}``; qui il gateway le prende da **un host fisso** e
le tiene su disco. La WebView non parla mai con l'esterno — la sua CSP resta
``img-src 'self'`` — e il client non sceglie l'indirizzo: dà tre numeri, che si
controllano.

**La fonte è OpenStreetMap**, le cui regole d'uso chiedono un User-Agent che
dica chi è, una cache che rispetti la scadenza (qui sette giorni) e niente uso
pesante. Il prezzo per l'utente: il server delle tessere vede la zona della
mappa (non chi la guarda). Cambiare fonte è cambiare ``TILE_URL``.

Senza rete si serve la copia scaduta, se c'è: una mappa di una settimana fa è
meglio di una scheda senza mappa. Senza nemmeno quella, ``None`` e la scheda
resta col disegno a curve di livello.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
from loguru import logger

from jenny.security.fetch import JENNY_USER_AGENT
from jenny.utils.path import atomic_write

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

MIN_ZOOM = 2
MAX_ZOOM = 18
TILE_TTL_S = 7 * 24 * 3600
_MAX_TILE_BYTES = 512 * 1024
_CACHE_MAX_BYTES = 20 * 1024 * 1024
# Il conto della cartella non si rifà a ogni tessera: ogni tanto basta.
_PRUNE_EVERY = 25
_TIMEOUT_S = 8.0
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_writes_since_prune = 0
_inflight: dict[tuple[int, int, int], asyncio.Future[bytes | None]] = {}


def valid_tile(z: int, x: int, y: int) -> bool:
    """Tre numeri che indicano una tessera che esiste."""
    if not MIN_ZOOM <= z <= MAX_ZOOM:
        return False
    n = 2**z
    return 0 <= x < n and 0 <= y < n


def _path(cache_dir: Path, z: int, x: int, y: int) -> Path:
    return cache_dir / str(z) / str(x) / f"{y}.png"


def _read(path: Path) -> tuple[bytes, float] | None:
    try:
        return path.read_bytes(), path.stat().st_mtime
    except OSError:
        return None


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Una tessera è una cache: niente fsync, ma il temporaneo è unico per
    # scrittura, così due richieste della stessa tessera non si pestano.
    atomic_write(path, data, fsync_file=False, fsync_dir=False)


def _prune(cache_dir: Path, max_bytes: int) -> None:
    """Toglie le tessere più vecchie finché la cartella sta sotto *max_bytes*."""
    files: list[tuple[float, int, Path]] = []
    total = 0
    for path in cache_dir.rglob("*.png"):
        try:
            st = path.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, path))
        total += st.st_size
    if total <= max_bytes:
        return
    files.sort()
    for _mtime, size, path in files:
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        if total <= max_bytes:
            break


async def _download(client: httpx.AsyncClient, z: int, x: int, y: int) -> bytes | None:
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        response = await client.get(url, headers={"User-Agent": JENNY_USER_AGENT})
    except httpx.HTTPError as exc:
        logger.debug("Map tile {}/{}/{}: {}", z, x, y, exc)
        return None
    body = response.content
    if response.status_code != 200 or len(body) > _MAX_TILE_BYTES:
        logger.debug("Map tile {}/{}/{}: HTTP {}", z, x, y, response.status_code)
        return None
    if not body.startswith(_PNG_MAGIC):
        return None
    return body


async def _fetch(cache_dir: Path, z: int, x: int, y: int, client: httpx.AsyncClient | None) -> bytes | None:
    global _writes_since_prune
    path = _path(cache_dir, z, x, y)
    cached = await asyncio.to_thread(_read, path)
    if cached is not None and time.time() - cached[1] < TILE_TTL_S:
        return cached[0]

    owns = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False)
    try:
        body = await _download(client, z, x, y)
    finally:
        if owns:
            await client.aclose()
    if body is None:
        return cached[0] if cached is not None else None

    await asyncio.to_thread(_write, path, body)
    _writes_since_prune += 1
    if _writes_since_prune >= _PRUNE_EVERY:
        _writes_since_prune = 0
        await asyncio.to_thread(_prune, cache_dir, _CACHE_MAX_BYTES)
    return body


async def get_tile(
    cache_dir: Path, z: int, x: int, y: int, *, client: httpx.AsyncClient | None = None
) -> bytes | None:
    """La tessera *z*/*x*/*y* come PNG, dalla cache o dalla rete; ``None`` se non
    c'è modo di averla. Due richieste della stessa tessera nello stesso momento
    (due schede sullo stesso posto) fanno un download solo."""
    if not valid_tile(z, x, y):
        return None
    key = (z, x, y)
    pending = _inflight.get(key)
    if pending is not None:
        return await asyncio.shield(pending)
    future: asyncio.Future[bytes | None] = asyncio.get_running_loop().create_future()
    _inflight[key] = future
    try:
        body = await _fetch(Path(cache_dir), z, x, y, client)
        future.set_result(body)
        return body
    except Exception:  # noqa: BLE001 — una tessera non vale un errore del gateway
        logger.opt(exception=True).debug("Map tile {}/{}/{} failed", z, x, y)
        future.set_result(None)
        return None
    finally:
        _inflight.pop(key, None)
