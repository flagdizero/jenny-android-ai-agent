"""RPC domanda→risposta verso un singolo client WebUI (ui_query/ui_result).

Il tool ``ui_view`` gira nel backend, ma la vista da descrivere vive nella
WebView. Il canale WebSocket sa solo *spingere* eventi verso i client, mai fare
una domanda e attendere. Questo coordinator colma il gap: registra una Future
per ogni ``correlation_id`` (legata alla connessione che riceve la query) e la
risolve quando il client risponde con un envelope ``ui_result``.

Modulo leaf accanto a ``ws_sender``/``ws_parsing``: nessuno stato del canale,
solo il registro delle richieste in volo. Ricalca il pattern di
``CronTurnCoordinator`` (:mod:`jenny.agent.cron_turns`).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger

from jenny.security.wire_ids import WIRE_ID_RE

# Valida i correlation-id "uiq-<uuid4hex>" generati qui: charset
# [A-Za-z0-9_-], nessun ':' nel formato.
_CORRELATION_RE = WIRE_ID_RE

# Cintura + bretelle contro un client ostile: il payload di una risposta oltre
# questa soglia viene rifiutato (il transport WS cappa comunque a max_size).
MAX_PAYLOAD_BYTES = 256 * 1024


class UiQueryUnavailableError(Exception):
    """Nessun client interrogabile: conn assente, disconnessa o payload rifiutato."""


class UiQueryTimeoutError(Exception):
    """Il client non ha risposto entro il timeout (app in background / schermo spento)."""


@dataclass
class _PendingQuery:
    conn_id: str
    future: asyncio.Future[dict[str, Any]]


class UiQueryCoordinator:
    """Registro correlation_id → Future per le query alla WebUI in volo."""

    def __init__(self) -> None:
        self._pending: dict[str, _PendingQuery] = {}
        self._channel: Any | None = None

    def set_channel(self, channel: Any) -> None:
        """Collega il canale WS (chiamato dal canale nel suo costruttore)."""
        self._channel = channel

    async def query(self, conn_id: str, *, timeout_s: float = 6.0) -> dict[str, Any]:
        """Interroga la connessione ``conn_id`` e attende il suo ``ui_result``.

        Raises:
            UiQueryUnavailableError: canale non collegato, connessione sparita o
                risposta rifiutata (payload oversize / errore del client).
            UiQueryTimeoutError: nessuna risposta entro ``timeout_s``.
        """
        if self._channel is None:
            raise UiQueryUnavailableError("no WebSocket channel bound")

        correlation_id = "uiq-" + uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[correlation_id] = _PendingQuery(conn_id, future)
        try:
            sent = await self._channel.send_ui_query(conn_id, correlation_id)
            if not sent:
                raise UiQueryUnavailableError("no WebUI client connected")
            try:
                return await asyncio.wait_for(future, timeout_s)
            except asyncio.TimeoutError as exc:
                raise UiQueryTimeoutError(
                    f"WebUI did not reply within {timeout_s:g}s"
                ) from exc
        finally:
            self._pending.pop(correlation_id, None)

    def handle_ui_result(self, conn_id: str, envelope: dict[str, Any]) -> None:
        """Risolve la Future in attesa dal frame ``ui_result`` del client.

        Rifiuta silenziosamente correlation-id sconosciuti/tardivi (una risposta
        arrivata dopo il timeout è attesa, non un errore) e — per sicurezza —
        una risposta che arriva da una connessione diversa da quella che ha
        ricevuto la query (un client non può risolvere le query di un altro).
        """
        raw = envelope.get("correlation_id")
        if not isinstance(raw, str) or not _CORRELATION_RE.match(raw):
            logger.warning("ui_result with invalid correlation_id from {}", conn_id)
            return
        pending = self._pending.get(raw)
        if pending is None:
            logger.debug("ui_result for unknown/expired correlation_id {}", raw)
            return
        if pending.conn_id != conn_id:
            # Non toccare la Future e non rivelarne l'esistenza al mittente.
            logger.warning(
                "ui_result for {} came from conn {} but query targeted {}",
                raw, conn_id, pending.conn_id,
            )
            return
        if pending.future.done():
            return  # duplicato / risposta tardiva già gestita

        error = envelope.get("error")
        if error is not None:
            pending.future.set_exception(
                UiQueryUnavailableError(f"client reported error: {error}")
            )
            return

        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            pending.future.set_exception(
                UiQueryUnavailableError("ui_result missing a payload object")
            )
            return
        try:
            size = len(json.dumps(payload).encode("utf-8"))
        except (TypeError, ValueError):
            pending.future.set_exception(
                UiQueryUnavailableError("ui_result payload is not JSON-serializable")
            )
            return
        if size > MAX_PAYLOAD_BYTES:
            pending.future.set_exception(
                UiQueryUnavailableError(
                    f"ui_result payload too large ({size} > {MAX_PAYLOAD_BYTES} bytes)"
                )
            )
            return
        pending.future.set_result(payload)

    def cancel_for_conn(self, conn_id: str) -> None:
        """Fallisce subito le query pendenti verso una connessione che si chiude."""
        for pending in list(self._pending.values()):
            if pending.conn_id == conn_id and not pending.future.done():
                pending.future.set_exception(
                    UiQueryUnavailableError("WebUI client disconnected")
                )
