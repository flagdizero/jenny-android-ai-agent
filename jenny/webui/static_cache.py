"""Impronte e compressione per i file statici della WebUI.

I file della SPA si servivano con ``Cache-Control: no-cache`` e **senza** un
validatore: «ricontrollami» senza un modo per ricontrollare, quindi il browser
li riscaricava tutti a ogni apertura. Dal Mac via Tailscale erano 117 richieste
e 3,3 MB per aprire la casa, sempre, con zero riusi (misurato il 15/10/2026).

Qui l'impronta (``ETag``) e il confronto con ``If-None-Match``: il browser
chiede «e' cambiato?» e riceve un 304 di pochi byte. ``no-cache`` resta: si
ricontrolla a ogni apertura, quindi un aggiornamento di Jenny arriva subito e
non si mischiano mai file di due versioni.

E la compressione, che ``GatewayHTTPHandler`` chiede solo per chi arriva da
fuori: sul telefono (loopback) costerebbe CPU per niente.

Tutto si calcola una volta: i byte canonici del package non cambiano finche' il
processo vive, e un file del disco si riconosce da percorso, dimensione e mtime.
"""

from __future__ import annotations

import gzip
import hashlib
from collections.abc import Hashable

# I tipi che vale la pena comprimere. Le immagini (webp, png) e i font (woff2)
# sono gia' compressi: rifarlo costa e non rende.
_COMPRESSIBLE = (
    "text/",
    "application/javascript",
    "application/json",
    "image/svg+xml",
)
# Sotto questa soglia l'intestazione gzip si mangia il guadagno.
_MIN_GZIP_BYTES = 1024

_ETAGS: dict[Hashable, str] = {}
_GZIPPED: dict[str, bytes] = {}


def etag_for(key: Hashable, body: bytes) -> str:
    """L'impronta forte di *body*, calcolata una volta per *key*.

    *key* deve cambiare quando cambiano i byte: per il package il percorso
    (immutabile per processo), per il disco ``(percorso, dimensione, mtime)``.
    """
    tag = _ETAGS.get(key)
    if tag is None:
        tag = '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'
        _ETAGS[key] = tag
    return tag


def matches(if_none_match: str | None, etag: str) -> bool:
    """Vero se l'``If-None-Match`` della richiesta nomina *etag* (o e' ``*``).

    Il confronto e' debole (RFC 9110 §13.1.2): ``W/"x"`` vale come ``"x"``.
    """
    if not if_none_match:
        return False
    wanted = etag.removeprefix("W/")
    for part in if_none_match.split(","):
        tag = part.strip()
        if tag == "*" or tag.removeprefix("W/") == wanted:
            return True
    return False


def compressible(content_type: str) -> bool:
    return content_type.startswith(_COMPRESSIBLE)


def accepts_gzip(accept_encoding: str | None) -> bool:
    """``Accept-Encoding`` contiene gzip, e non con ``q=0``."""
    for part in (accept_encoding or "").split(","):
        name, _, params = part.strip().partition(";")
        if name.strip().lower() != "gzip":
            continue
        q = params.strip().lower()
        if not q.startswith("q="):
            return True
        try:
            return float(q[2:]) > 0
        except ValueError:
            return False
    return False


def gzip_variant(etag: str, body: bytes) -> tuple[str, bytes] | None:
    """La versione compressa di *body* e la sua impronta, o ``None`` se non conviene.

    L'impronta del compresso e' un'altra (``"<hash>-gz"``): due rappresentazioni
    con lo stesso ``ETag`` confonderebbero una cache che le vede tutte e due.
    """
    if len(body) < _MIN_GZIP_BYTES:
        return None
    packed = _GZIPPED.get(etag)
    if packed is None:
        # mtime=0: lo stesso file da' sempre gli stessi byte compressi.
        packed = gzip.compress(body, compresslevel=6, mtime=0)
        _GZIPPED[etag] = packed
    if len(packed) >= len(body):
        return None
    return etag[:-1] + '-gz"', packed


def reset_static_cache() -> None:
    """Svuota le tabelle (per i test)."""
    _ETAGS.clear()
    _GZIPPED.clear()
