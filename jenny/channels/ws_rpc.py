"""RPC client→server sul canale WebSocket (``rpc`` / ``rpc_result``).

Immagine speculare di :mod:`jenny.channels.ui_query`: là il backend fa una
domanda alla WebView e attende, qui la WebView chiede al backend di eseguire un
comando e attende. Serve perché la superficie ``/api/`` del gateway non può
trasportare contenuto — è servita dall'hook di handshake di ``websockets``, che
non legge body, e i suoi header sono limitati a 8 KB per riga e a ISO-8859-1.
Il WebSocket invece è già framed e UTF-8, quindi un file con emoji dentro passa
senza trucchi.

Modulo leaf come ``ui_query``/``ws_parsing``: nessuno stato del canale, solo
validazione dell'envelope, autorizzazione e traduzione degli errori. La logica
dei comandi sta in :mod:`jenny.webui.commands`, che non sa nulla di trasporti.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from jenny.security.wire_ids import WIRE_ID_RE
from jenny.webui.commands import CommandContext, CommandError, dispatch_command

# Stesso charset dei correlation-id di ``ui_query``: il client genera
# "rpc-<uuid4hex>", ma qualunque token opaco corto va bene. Nessun ':' e nessuno
# spazio, così l'id non può confondersi con una session key nei log.
_ID_RE = WIRE_ID_RE

# Tetto sui nomi di metodo, prima di qualunque lookup: un client ostile non deve
# poter far loggare (né confrontare) stringhe arbitrariamente lunghe.
_MAX_METHOD_LEN = 64


class RpcFrameError(Exception):
    """Envelope ``rpc`` inutilizzabile: manca l'id, o non è un id valido.

    Distinta da ``CommandError``: senza un id valido non c'è nessuna richiesta a
    cui rispondere, quindi il canale non può che loggare e lasciar cadere il
    frame (come fa ``ui_query`` con un ``correlation_id`` malformato).
    """


def parse_rpc_frame(envelope: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Estrae ``(id, method, params)`` da un envelope ``rpc``.

    Raises:
        RpcFrameError: se l'id manca o è malformato (nessuna risposta possibile).
        CommandError: se l'id è valido ma il resto del frame non lo è — quello
            l'utente lo può vedere, perché c'è un id su cui rispondere.
    """
    raw_id = envelope.get("id")
    if not isinstance(raw_id, str) or not _ID_RE.match(raw_id):
        raise RpcFrameError(f"invalid rpc id: {raw_id!r}")

    method = envelope.get("method")
    if not isinstance(method, str) or not method.strip():
        raise CommandError("bad_request", "method required")
    if len(method) > _MAX_METHOD_LEN:
        raise CommandError("bad_request", "method too long")

    params = envelope.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise CommandError("bad_request", "params must be an object")
    return raw_id, method, params


def authorize(*, secret: str, connection_authenticated: bool) -> None:
    """Autorizza un RPC sulla connessione corrente.

    Il verdetto è quello dell'handshake, non del frame: se un secret è
    configurato, solo una connessione che ha presentato il token può mutare
    qualcosa. Senza questo controllo un setup con
    ``websocket_requires_token = false`` avrebbe una scrittura file più debole
    di ``/api/``, che invece fallisce chiuso quando il secret manca.

    Raises:
        CommandError: ``forbidden`` se la connessione non è autenticata.
    """
    if not secret.strip():
        return
    if not connection_authenticated:
        raise CommandError("forbidden", "unauthorized")


async def run_rpc(
    ctx: CommandContext,
    *,
    method: str,
    params: dict[str, Any],
    secret: str,
    connection_authenticated: bool,
) -> dict[str, Any]:
    """Autorizza ed esegue un comando. Solleva sempre ``CommandError`` in caso di errore."""
    authorize(secret=secret, connection_authenticated=connection_authenticated)
    return await dispatch_command(ctx, method, params)


def result_frame(rpc_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Campi del frame ``rpc_result`` riuscito (l'``event`` lo aggiunge il sender)."""
    return {"id": rpc_id, "ok": True, "result": result}


def error_frame(rpc_id: str, error: CommandError) -> dict[str, Any]:
    """Campi del frame ``rpc_result`` fallito: codice + messaggio, mai un traceback."""
    return {
        "id": rpc_id,
        "ok": False,
        "error": {"code": error.code, "message": error.message},
    }


def log_dropped_frame(exc: RpcFrameError, conn_id: str) -> None:
    """Un frame senza id valido non ha risposta: resta solo la riga di log."""
    logger.warning("dropped rpc frame from {}: {}", conn_id, exc)
