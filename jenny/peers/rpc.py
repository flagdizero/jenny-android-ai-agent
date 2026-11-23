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
from .model import ACT_LEVELS, is_id

# I comandi delle richieste di fare (``jenny.peers.acting``): rispondere a una
# conferma, annullare una richiesta arrivata qui, chiedere all'altra di
# annullarne una fatta da qui, leggere quelle arrivate.
ACT_METHODS = frozenset({"peer.confirm", "peer.undo", "peer.undo_remote", "peer.acts"})

# Il link che il QR porta: lo apre la fotocamera di sistema, e Android lo passa
# a Jenny (``intent-filter`` per ``jenny://pair`` nel manifest). Apre solo il
# modulo già compilato: l'abbinamento lo conferma l'utente.
PAIR_LINK_SCHEME = "jenny://pair"

_CODE_FOR = {
    "bad_code": "bad_request",
    "bad_name": "bad_request",
    "bad_level": "bad_request",
    "still_working": "unavailable",
    "nothing_to_undo": "not_found",
    "protocol": "bad_request",
    "not_found": "not_found",
    "disabled": "forbidden",
    "unreachable": "unavailable",
    "no_code_open": "unavailable",
    "closed": "unavailable",
    "refused": "unavailable",
    "no_secret": "unavailable",
    "no_peers": "not_found",
    "which_peer": "bad_request",
    "bad_place": "bad_request",
    "bad_channel": "bad_request",
    "no_line": "not_found",
}

# I comandi della linea passata (``jenny.peers.line``), dalla chat della casa.
LINE_METHODS = frozenset({
    "peer.line.status", "peer.line.open", "peer.line.close", "peer.line.move",
    "peer.line.chats", "peer.line.retry", "peer.line.say", "peer.line.answer",
})
# La chat della WebUI da cui si passa la linea: solo quella di sempre.
_LINE_CHANNEL = "websocket"
_LINE_CHAT = "default"


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


def _qid(params: dict[str, Any]) -> str:
    qid = params.get("qid")
    if not is_id(qid):
        raise CommandError("bad_request", "qid must be a request id")
    return qid  # type: ignore[return-value]


async def _act_command(link: PeerLink, method: str, params: dict[str, Any]) -> dict[str, Any]:
    acts = link.acts
    if acts is None:
        raise CommandError("unavailable", "requests to act are not running")
    if method == "peer.confirm":
        cid = params.get("cid")
        allow = params.get("allow")
        if not is_id(cid) or not isinstance(allow, bool):
            raise CommandError("bad_request", "cid and allow are required")
        return {"resolved": acts.confirmations.resolve(cid, allow)}
    if method == "peer.undo":
        return {"undo": await acts.undo(_peer_id(params), _qid(params))}
    if method == "peer.undo_remote":
        await acts.request_undo(_peer_id(params), _qid(params))
        return {"asked": True}
    peer_id = _peer_id(params)
    return {"acts": await acts.history(peer_id), "pending": acts.pending(peer_id)}


async def _line_command(link: PeerLink, method: str, params: dict[str, Any]) -> dict[str, Any]:
    lines = link.lines
    if lines is None:
        raise CommandError("unavailable", "lines are not running")
    if method == "peer.line.open":
        await lines.open(
            _LINE_CHANNEL, _LINE_CHAT, _peer_id(params), _text(params, "place", limit=64),
        )
    elif method == "peer.line.close":
        await lines.close(_LINE_CHANNEL, _LINE_CHAT)
    elif method == "peer.line.move":
        await lines.move(_LINE_CHANNEL, _LINE_CHAT, _text(params, "place", limit=64))
    elif method == "peer.line.retry":
        await lines.retry(_LINE_CHANNEL, _LINE_CHAT)
    elif method == "peer.line.say":
        # Quel che l'utente ha scritto nel campo, a linea aperta. Senza una
        # linea non diventa un messaggio per Jenny: chi chiama lo sa dal rifiuto.
        if not await lines.route(_LINE_CHANNEL, _LINE_CHAT, _text(params, "text", limit=8000)):
            raise PeerError("no_line")
    elif method == "peer.line.answer":
        # Il tocco su una domanda dell'altra Jenny disegnata nel filo.
        cid, choice = params.get("cid"), params.get("choice")
        consent = link.phone_consent
        if not is_id(cid) or choice not in ("ok", "always", "no") or consent is None:
            raise CommandError("bad_request", "cid and choice are required")
        return {"answered": bool(consent.answer(cid, choice))}
    elif method == "peer.line.chats":
        return {"chats": await lines.chats(_peer_id(params))}
    return {"line": await lines.status(_LINE_CHANNEL, _LINE_CHAT)}


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
            flags: dict[str, Any] = {}
            for key in ("can_ask", "send_files", "phone_confirm", "line", "line_trace"):
                if key in params:
                    if not isinstance(params[key], bool):
                        raise CommandError("bad_request", f"{key} must be a boolean")
                    flags[key] = params[key]
            if "act" in params:
                if params["act"] not in ACT_LEVELS:
                    raise CommandError("bad_request", "act must be off, confirm or free")
                flags["act"] = params["act"]
            await link.set_permissions(_peer_id(params), **flags)
        elif method in ACT_METHODS:
            extra.update(await _act_command(link, method, params))
        elif method in LINE_METHODS:
            extra.update(await _line_command(link, method, params))
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
