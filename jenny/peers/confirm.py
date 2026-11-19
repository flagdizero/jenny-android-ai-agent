"""Le conferme di una richiesta di fare: «Personale vuole aprire bianchi.it».

Il lavoro di una richiesta che sta per mandare qualcosa fuori dal telefono si
ferma qui (``jenny.peers.gate``) finché l'utente di questo telefono non dice
sì o no: dalla notifica (i tasti «Consenti» e «No», ``runtime.notifier``), dal
pannello dell'Officina (``peer.confirm``), o togliendo il permesso, che conta
come un no. Senza risposta entro :data:`CONFIRM_TTL_S` è un no.

Quello che si chiede lo tiene **il server**, come le sonde SSH
(``webui/ssh_api.py``): chi preme un tasto manda solo l'identificativo, casuale
e buono una volta. Un'app con l'accesso alle notifiche che premesse «Consenti»
al posto dell'utente consentirebbe solo quello che la notifica diceva.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from .store import now_ms

#: Quanto aspetta una conferma prima di valere «no».
CONFIRM_TTL_S = 600.0

Post = Callable[[str, str, str], None]
Clear = Callable[[str], None]


@dataclass
class _Pending:
    cid: str
    peer: str
    peer_name: str
    qid: str
    what: str
    at_ms: int
    future: asyncio.Future[bool] = field(repr=False)


class Confirmations:
    """Le conferme in attesa, in memoria: il lavoro che le aspetta è vivo."""

    def __init__(
        self,
        *,
        post: Post | None = None,
        clear: Clear | None = None,
        ttl_s: float = CONFIRM_TTL_S,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._post = post
        self._clear = clear
        self._ttl_s = ttl_s
        self._pending: dict[str, _Pending] = {}
        self.on_change = on_change

    def pending(self, peer: str | None = None) -> list[dict[str, Any]]:
        """Le conferme in attesa (di *peer*, o tutte), dalla più vecchia."""
        rows = sorted(self._pending.values(), key=lambda p: p.at_ms)
        return [
            {"cid": p.cid, "peer": p.peer, "qid": p.qid, "what": p.what, "at_ms": p.at_ms}
            for p in rows if peer is None or p.peer == peer
        ]

    async def ask(self, *, peer: str, peer_name: str, qid: str, what: str) -> bool:
        """Chiede all'utente di qui e aspetta: vero solo con un «Consenti»."""
        loop = asyncio.get_running_loop()
        cid = secrets.token_hex(16)
        pending = _Pending(
            cid=cid, peer=peer, peer_name=peer_name, qid=qid, what=what[:200],
            at_ms=now_ms(), future=loop.create_future(),
        )
        self._pending[cid] = pending
        self._notify(lambda: self._post and self._post(cid, peer_name, pending.what))
        self._changed()
        try:
            return await asyncio.wait_for(asyncio.shield(pending.future), self._ttl_s)
        except asyncio.TimeoutError:
            logger.info("peers: a confirmation for {} went unanswered", peer_name)
            return False
        finally:
            self._pending.pop(cid, None)
            if not pending.future.done():
                pending.future.cancel()
            self._notify(lambda: self._clear and self._clear(cid))
            self._changed()

    def resolve(self, cid: str, allow: bool) -> bool:
        """La risposta dell'utente; falso se quella conferma non c'è (più)."""
        pending = self._pending.get(cid)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(bool(allow))
        return True

    def deny_peer(self, peer: str) -> int:
        """Il permesso tolto: ogni conferma di quella Jenny diventa un no."""
        denied = 0
        for p in list(self._pending.values()):
            if p.peer == peer and not p.future.done():
                p.future.set_result(False)
                denied += 1
        return denied

    def _notify(self, call: Callable[[], Any]) -> None:
        try:
            call()
        except Exception:  # noqa: BLE001 — la notifica non ferma la conferma
            logger.opt(exception=True).debug("peers: confirmation notice failed")

    def _changed(self) -> None:
        if self.on_change is not None:
            self._notify(self.on_change)


__all__ = ["CONFIRM_TTL_S", "Confirmations"]
