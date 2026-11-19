"""I comandi ``peer.*`` dell'Officina: stato, abbinamento, rinomina, scollega, prova.

Li serve il canale WebSocket (``WebSocketChannel._handle_peer_rpc``), come
``remote.*``: accendere il collegamento vuol dire tenere su il secondo
ascoltatore, e quello è suo. Qui c'è solo la traduzione fra i parametri del
comando e :class:`~jenny.peers.link.PeerLink`, e fra i suoi rifiuti e i codici
chiusi di ``CommandError`` (la ragione precisa va nel messaggio: la UI la dice
nella sua lingua).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from jenny.webui.commands import CommandError

from .link import PeerError, PeerLink
from .model import is_id

# Il link che il QR porta: lo apre la fotocamera di sistema, e Android lo passa
# a Jenny (``intent-filter`` per ``jenny://pair`` nel manifest). Apre solo il
# modulo già compilato: l'abbinamento lo conferma l'utente.
PAIR_LINK_SCHEME = "jenny://pair"

_CODE_FOR = {
    "bad_code": "bad_request",
    "bad_name": "bad_request",
    "protocol": "bad_request",
    "not_found": "not_found",
    "disabled": "forbidden",
    "unreachable": "unavailable",
    "no_code_open": "unavailable",
    "closed": "unavailable",
    "refused": "unavailable",
    "no_secret": "unavailable",
}


def pair_link(*, host: str | None, ip: str | None, port: int, code: str) -> str:
    params = {k: v for k, v in (("host", host), ("ip", ip)) if v}
    params["port"] = str(port)
    params["code"] = code
    return f"{PAIR_LINK_SCHEME}?{urlencode(params)}"


def _peer_id(params: dict[str, Any]) -> str:
    peer_id = params.get("id")
    if not is_id(peer_id):
        raise CommandError("bad_request", "id must be a peer id")
    return peer_id  # type: ignore[return-value]


def _text(params: dict[str, Any], key: str, *, limit: int = 253) -> str:
    value = params.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CommandError("bad_request", f"{key} must be a string")
    return value.strip()[:limit]


async def handle(
    link: PeerLink,
    method: str,
    params: dict[str, Any],
    *,
    self_address: tuple[str | None, str | None],
    tailscale: dict[str, Any],
) -> dict[str, Any]:
    """Esegue *method* e ritorna lo stato aggiornato (più quel che il comando dà)."""
    extra: dict[str, Any] = {}
    try:
        if method == "peer.set":
            enabled = params.get("enabled")
            if not isinstance(enabled, bool):
                raise CommandError("bad_request", "enabled must be a boolean")
            await link.set_enabled(enabled)
        elif method == "peer.pair.start":
            started = await link.start_pairing()
            host, ip = self_address
            extra["pair"] = {
                "code": started["code"],
                "expires_ms": started["expires_ms"],
                "host": host,
                "ip": ip,
                "port": link.port,
                "link": pair_link(host=host, ip=ip, port=link.port, code=started["raw_code"]),
            }
        elif method == "peer.pair.cancel":
            link.cancel_pairing()
        elif method == "peer.pair.join":
            port = params.get("port")
            if port is not None and (not isinstance(port, int) or not 0 < port < 65536):
                raise CommandError("bad_request", "port must be a port number")
            host = _text(params, "host")
            ip = _text(params, "ip", limit=45)
            if not host and not ip:
                raise CommandError("bad_request", "host or ip is required")
            extra["joined"] = await link.join(
                host=host, ip=ip, port=port, code=_text(params, "code", limit=20),
                name=_text(params, "name", limit=80),
            )
        elif method == "peer.rename":
            await link.rename(_peer_id(params), _text(params, "name", limit=80))
        elif method == "peer.remove":
            await link.remove(_peer_id(params))
        elif method == "peer.permissions":
            flags = {}
            for key in ("can_ask", "send_files"):
                if key in params:
                    if not isinstance(params[key], bool):
                        raise CommandError("bad_request", f"{key} must be a boolean")
                    flags[key] = params[key]
            await link.set_permissions(_peer_id(params), **flags)
        elif method == "peer.ping":
            await link.ping(_peer_id(params))
        elif method == "peer.history":
            extra["history"] = await link.history(_peer_id(params))
        elif method != "peer.status":
            raise CommandError("not_found", f"unknown command: {method}")
    except PeerError as exc:
        raise CommandError(_CODE_FOR.get(exc.code, "internal"), exc.code) from exc
    status = await link.status()
    status["tailscale"] = tailscale
    status.update(extra)
    return status
