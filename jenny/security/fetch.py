"""Una GET che segue i redirect a mano e rivalida ogni salto (SSRF).

Stava scritta quattro volte — il manifest degli aggiornamenti
(``runtime/update_check.py``), la risoluzione dell'APK (``runtime/update_install.py``),
il tool ``download_file`` (``agent/tools/download.py``) e le immagini remote della
WebUI (``webui/media_ingest.py``) — con quattro ``MAX_REDIRECTS`` e due copie
dello stesso User-Agent. Il ciclo era lo stesso, le differenze no: l'https
imposto a ogni salto solo dall'APK, controllato solo sul primo URL dal manifest
(un redirect verso http passava), i messaggi d'errore scritti a mano. Qui c'è
una volta, con le differenze vere come parametri.

I redirect non si delegano a httpx perché ogni salto va rivalidato: un
``/releases/latest/download/`` di GitHub è per definizione un redirect verso un
altro host, e delegarli validerebbe solo il primo (``.agent/security.md``).

Il validatore lo passa il chiamante, e ha un default: ogni modulo lo importa per
nome da :mod:`jenny.security.network` e lo passa com'è al momento della
chiamata, così i test che lo sostituiscono modulo per modulo continuano a
sostituire proprio quello. Un validatore che dice sempre sì va scritto apposta.

Modulo a sé e non dentro ``network.py``: quello lo importa ``config/loader.py``
all'avvio, e questo si porta dietro ``httpx``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx

from jenny.security import network

# Salti oltre il primo. Cinque bastano per GitHub (release → CDN) con margine.
MAX_REDIRECTS = 5

# Un browser qualunque: certi CDN rispondono 403 a uno User-Agent da libreria.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Android 14; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

Validator = Callable[[str], tuple[bool, str | None]]


@asynccontextmanager
async def open_validated_stream(
    client: httpx.AsyncClient,
    url: str,
    *,
    validate: Validator | None = None,
    headers: dict[str, str] | None = None,
    https_only: bool = False,
) -> AsyncIterator[tuple[httpx.Response, str]]:
    """La risposta ``200`` in streaming e l'URL finale che l'ha data.

    Ogni salto passa da *validate* (``network.validate_url_target`` se non
    detto) e, con *https_only*, deve essere https: un downgrade a metà catena è
    un rifiuto, non un avviso. Solleva ``ValueError`` per un target rifiutato,
    un redirect senza ``Location``, uno stato diverso da 200 o troppi salti; le
    eccezioni di rete di ``httpx`` risalgono com'è. Chi vuole solo gli header
    esce dal ``with`` senza leggere il corpo, e la connessione si chiude.
    """
    check = validate or network.validate_url_target
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if https_only and not current.lower().startswith("https://"):
            raise ValueError("the URL must be https")
        ok, error = check(current)
        if not ok:
            raise ValueError(f"URL blocked: {error}")
        # ``headers=None`` e' il default di ``httpx``: passarlo sempre e' lo stesso
        # che ometterlo, e il tipo resta leggibile (``**extra`` era un dict che
        # pyright confrontava con ogni parametro di ``stream``).
        async with client.stream("GET", current, headers=headers) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect without a Location header")
                current = str(httpx.URL(current).join(location))
                continue
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            yield response, current
            return
    raise ValueError("too many redirects")


async def read_capped(response: httpx.Response, max_bytes: int, too_big: str) -> bytes:
    """Il corpo, ma non oltre *max_bytes*: oltre, ``ValueError(too_big)``.

    Si conta mentre arriva, non dopo: un server che manda più di quanto dichiara
    non riempie la memoria prima che ce ne si accorga.
    """
    data = bytearray()
    async for chunk in response.aiter_bytes():
        data.extend(chunk)
        if len(data) > max_bytes:
            raise ValueError(too_big)
    return bytes(data)
