"""Il consenso alle mani di Jenny, quando a chiedere è un'altra Jenny.

Il caso: dal telefono personale «chiedi a Lavoro di leggermi le email». Lavoro
sta per aprire Gmail e deve chiedere — ma davanti a quel telefono non c'è
nessuno. La domanda (``jenny.phone.consent``) allora viaggia fino al telefono
di chi ha chiesto, e la risposta torna indietro.

Tre messaggi sul collegamento:

* ``phone_ask`` — da chi agisce a chi ha chiesto: «sto per usare Gmail»;
* ``phone_reply`` — indietro: ``ok``, ``always`` o ``no``;
* ``phone_closed`` — la domanda non aspetta più (risposta arrivata, scaduta).

**Chi agisce decide se accettarlo.** Una risposta dall'altra Jenny vale solo se
su questo telefono quella Jenny ha ``phoneConfirm`` acceso
(``PeerConfig.phone_confirm``, spento di default) — riletto quando la risposta
arriva, non quando la domanda parte: toglierlo a metà è un no. Senza, la
domanda resta dov'era: la notifica su questo telefono.

**La risposta è un tocco, non un testo.** Di qua la domanda è una notifica
(e la pillola, se c'è) disegnata dal codice; il modello dell'altra Jenny non la
vede e non può rispondere al posto dell'utente. E vale solo per la domanda che
questo telefono ha davvero in attesa per *quella* Jenny: un identificativo
indovinato o di un'altra richiesta non apre niente.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loguru import logger

from jenny.phone import consent

from .model import is_id, text_field

KIND_PHONE_ASK = "phone_ask"
KIND_PHONE_REPLY = "phone_reply"
KIND_PHONE_CLOSED = "phone_closed"

_CHOICES = ("ok", "always", "no")


def _line_peer(ask: consent.Ask) -> str | None:
    """La Jenny attraverso cui l'utente sta scrivendo (``jenny.peers.line``), o ``None``."""
    peer = ask.metadata.get("_line_peer")
    return peer if is_id(peer) else None


def _asking_peer(ask: consent.Ask) -> str | None:
    """La Jenny a cui la domanda va portata: quella per cui il turno lavora
    (``_peer_act`` nei metadata), o quella da cui l'utente sta scrivendo."""
    act = ask.metadata.get("_peer_act")
    peer = act.get("peer") if isinstance(act, dict) else None
    return peer if is_id(peer) else _line_peer(ask)


def _crosses(ask: consent.Ask) -> bool:
    """Vero se la domanda è di un turno arrivato da un'altra Jenny.

    Una richiesta di fare (``origin`` ``peer``), o una linea passata: lì chi
    scrive è l'utente, ma ha in mano l'altro telefono, e la domanda mostrata
    solo su questo non la vedrebbe nessuno.
    """
    return ask.origin == "peer" or _line_peer(ask) is not None


class PeerPhoneConsent:
    """I due lati della domanda che attraversa il collegamento."""

    def __init__(
        self,
        link: Any,
        asks: consent.PhoneAsks,
        *,
        show: Callable[[str, str, dict[str, Any]], None],
        hide: Callable[[str], None],
        spawn: Callable[[Any], None],
    ) -> None:
        self._link = link
        self._asks = asks
        self._show = show
        self._hide = hide
        self._spawn = spawn
        # Le domande arrivate da un'altra Jenny e mostrate qui: cid -> chi l'ha mandata.
        self._incoming: dict[str, str] = {}
        # La stessa domanda disegnata anche nel filo di una linea aperta con
        # quella Jenny (``PeerLines.show_ask`` / ``hide_ask``): le lega il
        # composition root. Arrivano l'id della Jenny, l'id della domanda e,
        # per mostrarla, la domanda.
        self.line_show: Callable[[str, str, dict[str, Any]], Any] | None = None
        self.line_hide: Callable[[str, str], Any] | None = None
        link.phone_consent = self
        link.register(KIND_PHONE_ASK, self._on_ask)
        link.register(KIND_PHONE_REPLY, self._on_reply)
        link.register(KIND_PHONE_CLOSED, self._on_closed)
        asks.posters.append(self._forward)
        asks.clearers.append(self._closed)

    async def _allowed(self, peer_id: str, *, line: bool = False) -> bool:
        """Quella Jenny può portare la risposta: per una richiesta di fare con
        ``phoneConfirm`` acceso; per una linea, con la linea permessa — lì a
        rispondere è l'utente, che sta scrivendo da quel telefono."""
        cfg = await self._link._config()
        if not cfg.enabled:
            return False
        for p in cfg.peers:
            if p.id == peer_id:
                return bool(p.line) if line else bool(p.phone_confirm and p.act != "off")
        return False

    # -- Chi agisce: la domanda parte, la risposta torna ------------------------------

    def _forward(self, ask: consent.Ask) -> None:
        peer = _asking_peer(ask)
        if not _crosses(ask) or peer is None:
            return
        line = _line_peer(ask) is not None

        async def send() -> None:
            if not await self._allowed(peer, line=line):
                return
            await self._link.send(peer, KIND_PHONE_ASK, ask.public())

        self._spawn(send())

    def _closed(self, ask: consent.Ask) -> None:
        peer = _asking_peer(ask)
        if not _crosses(ask) or peer is None:
            return
        line = _line_peer(ask) is not None

        async def send() -> None:
            if await self._allowed(peer, line=line):
                await self._link.send(peer, KIND_PHONE_CLOSED, {"cid": ask.cid})

        self._spawn(send())

    async def _on_reply(self, peer_id: str, body: dict[str, Any]) -> None:
        cid, choice = body.get("cid"), body.get("choice")
        if not is_id(cid) or choice not in _CHOICES:
            raise ValueError("bad phone reply")
        ask = self._asks.get(cid)
        if ask is None or _asking_peer(ask) != peer_id:
            logger.info("peers: a phone reply from {} matched no question of hers", peer_id[:8])
            return
        if not await self._allowed(peer_id, line=_line_peer(ask) is not None):
            logger.info("peers: {} may not confirm phone use; reply ignored", peer_id[:8])
            return
        self._asks.resolve(cid, choice)

    # -- Chi ha chiesto: la domanda compare, il tocco riparte ---------------------------

    async def _on_ask(self, peer_id: str, body: dict[str, Any]) -> None:
        cid = body.get("cid")
        if not is_id(cid):
            raise ValueError("bad phone ask")
        options = [o for o in (body.get("options") or []) if o in _CHOICES]
        if "ok" not in options:
            raise ValueError("bad phone ask")
        cfg = await self._link._config()
        name = next((p.name for p in cfg.peers if p.id == peer_id), "")
        self._incoming[cid] = peer_id
        shown = {
            "kind": "costly" if body.get("kind") == "costly" else "app",
            "app": text_field(body, "app", limit=80),
            "goal": text_field(body, "goal", limit=240),
            "what": text_field(body, "what", limit=80), "options": options,
        }
        self._show(cid, name, shown)
        if self.line_show is not None:
            self._spawn(self.line_show(peer_id, cid, shown))

    async def _on_closed(self, peer_id: str, body: dict[str, Any]) -> None:
        cid = body.get("cid")
        if is_id(cid) and self._incoming.get(cid) == peer_id:
            del self._incoming[cid]
            self._hide(cid)
            if self.line_hide is not None:
                self._spawn(self.line_hide(peer_id, cid))

    def answer(self, cid: str, choice: str) -> bool:
        """Il tocco dell'utente su una domanda arrivata da un'altra Jenny. Falso
        se non è una di quelle."""
        peer_id = self._incoming.pop(cid, None)
        if peer_id is None or choice not in _CHOICES:
            return False
        self._hide(cid)
        if self.line_hide is not None:
            self._spawn(self.line_hide(peer_id, cid))
        self._spawn(self._link.send(peer_id, KIND_PHONE_REPLY, {"cid": cid, "choice": choice}))
        return True
