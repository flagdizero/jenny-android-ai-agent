"""Il protocollo fra due Jenny: percorso, frame, limiti.

Ogni frame è un oggetto JSON con un ``type``. Prima dell'autenticazione ne
valgono pochi e in un ordine fisso (:mod:`jenny.peers.auth`); dopo, solo
``msg``, ``ack`` e ``nack``. Un frame che non è fra questi chiude la
connessione: un'altra Jenny parla questo protocollo e nient'altro, e un frame
inatteso è più probabilmente qualcuno che prova che un errore da tollerare.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeGuard

# Il percorso sul secondo ascoltatore. La WebView usa il suo (``websocket.path``):
# i due non si incrociano mai, in nessuna delle due direzioni.
PEER_PATH = "/peer"

PROTOCOL_VERSION = 1

# Un frame più grande di così non è di un'altra Jenny. Le domande e i resoconti
# stanno in poche migliaia di caratteri; il tetto lascia margine a un testo lungo
# senza far tenere in memoria a nessuno un frame da megabyte.
MAX_FRAME_BYTES = 256 * 1024

# Gli id di una Jenny e dei messaggi: esadecimali, lunghezza fissa. Si
# confrontano con una regex prima di finire in un percorso o in una query.
_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")

# I tipi di messaggio che questa versione sa trattare. Un ``msg`` di un altro
# tipo riceve un ``nack`` («non lo conosco»): chi lo manda lo toglie dalla coda e
# lo scrive nella cronologia, invece di riprovarlo per sempre.
KIND_PING = "ping"
KIND_UNPAIR = "unpair"
KNOWN_KINDS = frozenset({KIND_PING, KIND_UNPAIR})


class FrameError(ValueError):
    """Un frame che non è del protocollo."""


def is_id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def encode(frame: dict[str, Any]) -> str:
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


def decode(raw: str | bytes, *expected: str) -> dict[str, Any]:
    """Il frame in *raw*, se è uno dei tipi *expected*; altrimenti :class:`FrameError`."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_FRAME_BYTES:
            raise FrameError("frame too large")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FrameError("frame is not utf-8") from exc
    if len(raw) > MAX_FRAME_BYTES:
        raise FrameError("frame too large")
    try:
        frame = json.loads(raw)
    except ValueError as exc:
        raise FrameError("frame is not json") from exc
    if not isinstance(frame, dict):
        raise FrameError("frame is not an object")
    kind = frame.get("type")
    if kind not in expected:
        raise FrameError(f"unexpected frame type {kind!r}")
    return frame


def text_field(frame: dict[str, Any], key: str, *, limit: int) -> str:
    """Un campo di testo del frame, tagliato a *limit*; ``""`` se manca o non è testo."""
    value = frame.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]
