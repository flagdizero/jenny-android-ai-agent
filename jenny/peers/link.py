"""Il collegamento fra due Jenny: abbinamento, consegna e ricezione.

:class:`PeerLink` fa tutto quello che passa sul filo e niente di quello che se
ne fa dopo: riceve un ``msg`` e lo passa al gestore del suo ``kind`` (oggi
``ping`` e ``unpair``; le domande e i resoconti li aggiungono i passi
successivi), manda quello che è in coda e lo toglie solo all'``ack``.

**Chi apre.** Una connessione si apre solo quando c'è qualcosa da mandare, e
quando è aperta serve tutti e due i versi: chi chiama manda la sua coda, chi
ascolta manda la sua, e ognuno dice ``done`` quando ha finito. Niente
connessioni lunghe: su un telefono una connessione aperta per ore muore al
primo cambio di rete, e la coda copre già il caso «l'altra non c'è».

**Dove.** Si chiama solo un indirizzo della tailnet (:func:`is_tailnet_ip`),
risolto qui e usato così com'è: un nome che punta fuori dalla tailnet non
riceve nemmeno l'``hello``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.channels.ws_remote import is_tailnet_ip
from jenny.snapshot.crypto import decrypt_container, encrypt_container
from jenny.snapshot.crypto_backends.base import CryptoAuthError

from . import auth, model
from .model import KIND_PING, KIND_UNPAIR, FrameError, is_id, text_field
from .store import (
    SECRET_BYTES,
    PeerStore,
    delete_secret,
    now_ms,
    read_secret,
    self_id,
    write_secret,
)

HANDSHAKE_TIMEOUT_S = 15.0
ACK_TIMEOUT_S = 30.0
DONE_TIMEOUT_S = 30.0
DIAL_TIMEOUT_S = 10.0
# La coda si ricontrolla almeno ogni cinque minuti anche senza sveglie: un
# ``kick`` perso non deve lasciare un messaggio fermo fino alla prossima.
IDLE_CHECK_S = 300.0
# Quanto si aspetta l'altra Jenny quando la si scollega: un saluto, non un
# motivo per far aspettare l'utente.
UNPAIR_NOTIFY_S = 8.0
NAME_MAX = 40

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]
Dial = Callable[[str], Awaitable[Any]]
Resolve = Callable[[str, int], Awaitable[list[str]]]
SelfAddress = Callable[[], Awaitable[tuple[str | None, str | None]]]


class PeerError(Exception):
    """Un'operazione sul collegamento non è riuscita; ``code`` è per la UI."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass
class _Pairing:
    code: str
    salt: str
    expires_ms: int
    attempts: int = 0


async def _ws_dial(uri: str) -> Any:
    from websockets.asyncio.client import connect

    return await connect(
        uri,
        open_timeout=DIAL_TIMEOUT_S,
        max_size=model.MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
    )


async def _resolve(host: str, port: int) -> list[str]:
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    infos = await asyncio.wait_for(
        asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM),
        timeout=DIAL_TIMEOUT_S,
    )
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


async def _no_address() -> tuple[str | None, str | None]:
    return None, None


def _uri(addr: str, port: int) -> str:
    host = f"[{addr}]" if ":" in addr else addr
    return f"ws://{host}:{port}{model.PEER_PATH}"


def clean_name(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())[:NAME_MAX]


class PeerLink:
    """Le altre Jenny di questo gateway: abbinarle, scriverle, ascoltarle."""

    def __init__(
        self,
        workspace: Path,
        *,
        port: int,
        dial: Dial | None = None,
        resolve: Resolve | None = None,
        allow_address: Callable[[str], bool] = is_tailnet_ip,
        self_address: SelfAddress | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self.port = port
        self.store = PeerStore(self._workspace)
        self._dial = dial or _ws_dial
        self._resolve = resolve or _resolve
        self._allow = allow_address
        # Come questa Jenny si presenta a un'altra (nome MagicDNS, ``100.x``).
        # Pubblico: lo imposta chi possiede l'ascoltatore, che sa i suoi indirizzi.
        self.self_address: SelfAddress = self_address or _no_address
        self._config_path = config_path
        self._handlers: dict[str, Handler] = {
            KIND_PING: self._on_ping,
            KIND_UNPAIR: self._on_unpair,
        }
        self._pairing: _Pairing | None = None
        self._kick = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._wakes = 0
        # Dopo un abbinamento o uno scollegamento: chi possiede l'ascoltatore lo
        # riporta allo stato di config (``WebSocketChannel.apply_remote_access``).
        self.on_change: Callable[[], Awaitable[None]] | None = None

    # -- Config ---------------------------------------------------------------

    def _load(self) -> Any:
        from jenny.config.loader import load_config

        return load_config(self._config_path).peers

    async def _config(self) -> Any:
        return await asyncio.to_thread(self._load)

    async def _peer(self, peer_id: str) -> Any | None:
        cfg = await self._config()
        return next((p for p in cfg.peers if p.id == peer_id), None)

    async def _mutate(self, apply: Callable[[Any], bool | None]) -> None:
        from jenny.config import store

        await store.mutate(lambda config: apply(config.peers), config_path=self._config_path)

    async def _changed(self) -> None:
        if self.on_change is None:
            return
        try:
            await self.on_change()
        except Exception:
            logger.opt(exception=True).warning("peers: on_change failed")

    def self_id(self) -> str:
        return self_id(self._workspace)

    def register(self, kind: str, handler: Handler) -> None:
        """Il gestore dei messaggi di tipo *kind*; uno per tipo."""
        if kind in self._handlers:
            raise ValueError(f"handler for {kind!r} already registered")
        self._handlers[kind] = handler

    # -- Ciclo di vita ----------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._pump(), name="peers-pump")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def kick(self) -> None:
        """Il prossimo giro della coda parte subito."""
        self._kick.set()

    def wake(self, peer_id: str | None = None) -> None:
        """Quel che aspetta un nuovo tentativo parte adesso (Tailscale tornato, l'utente insiste)."""
        # Contata: una sveglia che arriva mentre un tentativo sta fallendo non
        # deve finire sotto il rinvio che quel tentativo scrive a fine giro.
        self._wakes += 1
        try:
            self.store.wake(peer_id)
        except Exception:
            logger.opt(exception=True).warning("peers: wake failed")
        self.kick()

    # -- Stato per l'Officina ---------------------------------------------------

    async def status(self) -> dict[str, Any]:
        cfg = await self._config()
        peers = []
        for p in cfg.peers:
            state = await asyncio.to_thread(self.store.state, p.id)
            queued = await asyncio.to_thread(self.store.queued_count, p.id)
            peers.append({
                "id": p.id,
                "name": p.name,
                "host": p.host,
                "ip": p.ip,
                "port": p.port,
                "has_secret": read_secret(self._workspace, p.id) is not None,
                "queued": queued,
                **state,
            })
        pairing = self._live_pairing()
        return {
            "enabled": cfg.enabled,
            "self_id": self.self_id(),
            "peers": peers,
            "pairing": (
                {"code": auth.format_pair_code(pairing.code), "expires_ms": pairing.expires_ms}
                if pairing is not None
                else None
            ),
        }

    async def history(self, peer_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.recent_log, peer_id, limit)

    # -- Gesti dell'utente ------------------------------------------------------

    async def set_enabled(self, enabled: bool) -> None:
        def apply(peers: Any) -> bool:
            if peers.enabled == enabled:
                return False
            peers.enabled = enabled
            return True

        await self._mutate(apply)
        if not enabled:
            self._pairing = None
        await self._changed()
        self.kick()

    async def rename(self, peer_id: str, name: str) -> None:
        name = clean_name(name)
        if not name:
            raise PeerError("bad_name")

        def apply(peers: Any) -> bool:
            for p in peers.peers:
                if p.id == peer_id:
                    if p.name == name:
                        return False
                    p.name = name
                    return True
            raise PeerError("not_found")

        await self._mutate(apply)

    async def ping(self, peer_id: str) -> None:
        if await self._peer(peer_id) is None:
            raise PeerError("not_found")
        await asyncio.to_thread(self.store.enqueue, peer_id, KIND_PING, {})
        await asyncio.to_thread(self.store.log, peer_id, "out", KIND_PING, {})
        self.wake(peer_id)

    async def send(self, peer_id: str, kind: str, body: dict[str, Any]) -> str:
        """Mette in coda un messaggio per *peer_id*; parte appena l'altra c'è."""
        if await self._peer(peer_id) is None:
            raise PeerError("not_found")
        msg_id = await asyncio.to_thread(self.store.enqueue, peer_id, kind, body)
        self.kick()
        return msg_id

    async def remove(self, peer_id: str, *, notify: bool = True) -> None:
        """Scollega *peer_id*: un saluto all'altra se risponde, poi via tutto da qui."""
        peer = await self._peer(peer_id)
        if peer is None:
            raise PeerError("not_found")
        if notify and read_secret(self._workspace, peer_id) is not None:
            await asyncio.to_thread(self.store.enqueue, peer_id, KIND_UNPAIR, {})
            try:
                await asyncio.wait_for(self._deliver(peer), timeout=UNPAIR_NOTIFY_S)
            except (asyncio.TimeoutError, Exception):
                logger.info("peers: {} not reachable to say goodbye", peer_id[:8])
        await self._forget(peer_id)
        await asyncio.to_thread(self.store.log, peer_id, "sys", "removed", {})

    async def _forget(self, peer_id: str) -> None:
        def apply(peers: Any) -> bool:
            before = len(peers.peers)
            peers.peers = [p for p in peers.peers if p.id != peer_id]
            return len(peers.peers) != before

        await self._mutate(apply)
        delete_secret(self._workspace, peer_id)
        await asyncio.to_thread(self.store.drop_peer, peer_id)
        await self._changed()

    # -- Abbinamento: chi mostra il codice ----------------------------------------

    def _live_pairing(self) -> _Pairing | None:
        p = self._pairing
        if p is not None and now_ms() > p.expires_ms:
            self._pairing = p = None
        return p

    async def start_pairing(self) -> dict[str, Any]:
        """Un codice nuovo, valido :data:`auth.PAIR_TTL_MS`; il precedente non vale più."""
        cfg = await self._config()
        if not cfg.enabled:
            raise PeerError("disabled")
        self._pairing = _Pairing(
            code=auth.new_pair_code(),
            salt=auth.new_salt(),
            expires_ms=now_ms() + auth.PAIR_TTL_MS,
        )
        return {
            "code": auth.format_pair_code(self._pairing.code),
            "raw_code": self._pairing.code,
            "expires_ms": self._pairing.expires_ms,
        }

    def cancel_pairing(self) -> None:
        self._pairing = None

    async def _serve_pairing(self, ws: Any, first: dict[str, Any], remote_ip: str | None) -> None:
        pairing = self._live_pairing()
        peer_id = first.get("from")
        nonce_b = first.get("nonce")
        mine = self.self_id()
        if pairing is None or not is_id(peer_id) or peer_id == mine or not auth.is_hex(
            nonce_b, auth.NONCE_BYTES
        ):
            logger.info("peers: pairing refused (no code open or bad hello)")
            return
        nonce_a = auth.new_nonce()
        await ws.send(model.encode({
            "type": "pair_challenge", "from": mine, "nonce": nonce_a, "salt": pairing.salt,
        }))
        proof = await self._recv(ws, "pair_proof")
        key = await asyncio.to_thread(auth.code_key, pairing.code, pairing.salt)
        if not auth.mac_ok(key, proof.get("mac"), "pair-b", peer_id, mine, nonce_b, nonce_a):
            pairing.attempts += 1
            logger.warning("peers: wrong pairing code ({} of {})", pairing.attempts,
                           auth.PAIR_MAX_ATTEMPTS)
            if pairing.attempts >= auth.PAIR_MAX_ATTEMPTS:
                self._pairing = None
            return
        secret = secrets.token_bytes(SECRET_BYTES)
        sealed = await encrypt_container(key.hex(), secret, iterations=1)
        await ws.send(model.encode({
            "type": "pair_ok",
            "mac": auth.mac(key, "pair-a", mine, peer_id, nonce_a, nonce_b),
            "sealed": auth.b64(sealed),
        }))
        done = await self._recv(ws, "pair_done")
        if not auth.mac_ok(secret, done.get("mac"), "pair-done", peer_id, mine, nonce_b, nonce_a):
            logger.warning("peers: pairing not confirmed by the other Jenny")
            return
        self._pairing = None
        port = first.get("port")
        await self._save_peer(
            peer_id,
            secret,
            name=clean_name(first.get("name")) or "Jenny",
            host=text_field(first, "host", limit=253),
            ip=remote_ip or text_field(first, "ip", limit=45),
            port=port if isinstance(port, int) and 0 < port < 65536 else self.port,
        )

    # -- Abbinamento: chi ha il codice ---------------------------------------------

    async def join(
        self, *, host: str, ip: str, port: int | None, code: str, name: str
    ) -> dict[str, Any]:
        """Si abbina alla Jenny che mostra *code* a *host*/*ip*."""
        cfg = await self._config()
        if not cfg.enabled:
            raise PeerError("disabled")
        raw = auth.normalize_pair_code(code)
        if not raw:
            raise PeerError("bad_code")
        name = clean_name(name)
        if not name:
            raise PeerError("bad_name")
        host = (host or "").strip()
        ip = (ip or "").strip()
        port = port or self.port
        ws, used_ip = await self._connect([host, ip], port)
        try:
            mine = self.self_id()
            own_host, own_ip = await self.self_address()
            nonce_b = auth.new_nonce()
            await ws.send(model.encode({
                "type": "pair", "v": model.PROTOCOL_VERSION, "from": mine, "nonce": nonce_b,
                "name": own_host.split(".", 1)[0] if own_host else "",
                "host": own_host or "", "ip": own_ip or "", "port": self.port,
            }))
            try:
                challenge = await self._recv(ws, "pair_challenge")
            except PeerError as exc:
                raise PeerError("no_code_open") from exc
            peer_id = challenge.get("from")
            nonce_a = challenge.get("nonce")
            salt = challenge.get("salt")
            if (not is_id(peer_id) or peer_id == mine
                    or not auth.is_hex(nonce_a, auth.NONCE_BYTES)
                    or not auth.is_hex(salt, auth.SALT_BYTES)):
                raise PeerError("protocol")
            key = await asyncio.to_thread(auth.code_key, raw, salt)
            await ws.send(model.encode({
                "type": "pair_proof",
                "mac": auth.mac(key, "pair-b", mine, peer_id, nonce_b, nonce_a),
            }))
            try:
                ok = await self._recv(ws, "pair_ok")
            except PeerError as exc:
                raise PeerError("bad_code") from exc
            if not auth.mac_ok(key, ok.get("mac"), "pair-a", peer_id, mine, nonce_a, nonce_b):
                raise PeerError("bad_code")
            sealed = auth.unb64(ok.get("sealed"))
            if sealed is None:
                raise PeerError("protocol")
            try:
                secret = await decrypt_container(key.hex(), sealed)
            except CryptoAuthError as exc:
                raise PeerError("protocol") from exc
            if len(secret) != SECRET_BYTES:
                raise PeerError("protocol")
            await ws.send(model.encode({
                "type": "pair_done",
                "mac": auth.mac(secret, "pair-done", mine, peer_id, nonce_b, nonce_a),
            }))
        finally:
            await _close(ws)
        await self._save_peer(peer_id, secret, name=name, host=host, ip=used_ip or ip, port=port)
        return {"id": peer_id, "name": name}

    async def _save_peer(
        self, peer_id: str, secret: bytes, *, name: str, host: str, ip: str, port: int
    ) -> None:
        from jenny.config.schema import PeerConfig

        write_secret(self._workspace, peer_id, secret)

        def apply(peers: Any) -> bool:
            entry = PeerConfig(id=peer_id, name=name, host=host, ip=ip, port=port)
            peers.peers = [p for p in peers.peers if p.id != peer_id] + [entry]
            peers.enabled = True
            return True

        await self._mutate(apply)
        await asyncio.to_thread(self.store.note_seen, peer_id, now_ms())
        await asyncio.to_thread(self.store.log, peer_id, "sys", "paired", {"name": name})
        logger.info("peers: paired with {} ({})", name, peer_id[:8])
        await self._changed()

    # -- Il filo ----------------------------------------------------------------------

    async def _connect(self, targets: list[str], port: int) -> tuple[Any, str]:
        """Una connessione al primo di *targets* che risponde sulla tailnet."""
        last = "no address"
        for target in targets:
            if not target:
                continue
            try:
                addrs = await self._resolve(target, port)
            except (OSError, asyncio.TimeoutError) as exc:
                last = f"cannot resolve {target}: {exc}"
                continue
            for addr in addrs:
                if not self._allow(addr):
                    last = f"{target} is not on the tailnet"
                    continue
                try:
                    return await self._dial(_uri(addr, port)), addr
                except (OSError, asyncio.TimeoutError, Exception) as exc:
                    last = f"{addr}: {type(exc).__name__}"
        raise PeerError("unreachable", last)

    async def _recv(self, ws: Any, *expected: str) -> dict[str, Any]:
        from websockets.exceptions import ConnectionClosed

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=HANDSHAKE_TIMEOUT_S)
        except (asyncio.TimeoutError, ConnectionClosed) as exc:
            raise PeerError("closed") from exc
        try:
            return model.decode(raw, *expected)
        except FrameError as exc:
            raise PeerError("protocol", str(exc)) from exc

    async def serve(self, ws: Any, remote_ip: str | None = None) -> None:
        """Una connessione arrivata sul percorso ``/peer`` (lato di chi ascolta)."""
        try:
            first = await self._recv(ws, "pair", "hello")
            cfg = await self._config()
            if not cfg.enabled:
                return
            if first["type"] == "pair":
                await self._serve_pairing(ws, first, remote_ip)
            else:
                await self._serve_session(ws, first, remote_ip)
        except PeerError as exc:
            logger.info("peers: incoming connection ended ({})", exc.code)
        except Exception:
            logger.opt(exception=True).warning("peers: incoming connection failed")
        finally:
            await _close(ws)

    async def _serve_session(self, ws: Any, hello: dict[str, Any], remote_ip: str | None) -> None:
        peer_id = hello.get("from")
        nonce_d = hello.get("nonce")
        mine = self.self_id()
        if not is_id(peer_id) or hello.get("to") != mine or not auth.is_hex(
            nonce_d, auth.NONCE_BYTES
        ):
            return
        peer = await self._peer(peer_id)
        secret = read_secret(self._workspace, peer_id) if peer is not None else None
        if peer is None or secret is None:
            logger.info("peers: hello from an unknown Jenny, closing")
            return
        nonce_l = auth.new_nonce()
        await ws.send(model.encode({
            "type": "challenge",
            "nonce": nonce_l,
            "mac": auth.mac(secret, "l", mine, peer_id, nonce_d, nonce_l),
        }))
        proof = await self._recv(ws, "proof")
        if not auth.mac_ok(secret, proof.get("mac"), "d", peer_id, mine, nonce_d, nonce_l):
            logger.warning("peers: failed proof from {}", peer_id[:8])
            return
        await ws.send(model.encode({"type": "ready"}))
        await asyncio.to_thread(self.store.note_seen, peer_id, now_ms())
        if remote_ip and peer.ip != remote_ip and is_tailnet_ip(remote_ip):
            await self._remember_ip(peer_id, remote_ip)
        await self._exchange(ws, peer_id, initiator=False)

    async def _remember_ip(self, peer_id: str, ip: str) -> None:
        def apply(peers: Any) -> bool:
            for p in peers.peers:
                if p.id == peer_id and p.ip != ip:
                    p.ip = ip
                    return True
            return False

        try:
            await self._mutate(apply)
        except Exception:
            logger.opt(exception=True).debug("peers: could not update the address")

    async def _open_session(self, peer: Any) -> Any:
        secret = read_secret(self._workspace, peer.id)
        if secret is None:
            raise PeerError("no_secret")
        ws, _ = await self._connect([peer.host, peer.ip], peer.port)
        try:
            mine = self.self_id()
            nonce_d = auth.new_nonce()
            await ws.send(model.encode({
                "type": "hello", "v": model.PROTOCOL_VERSION,
                "from": mine, "to": peer.id, "nonce": nonce_d,
            }))
            challenge = await self._recv(ws, "challenge")
            nonce_l = challenge.get("nonce")
            if not auth.is_hex(nonce_l, auth.NONCE_BYTES) or not auth.mac_ok(
                secret, challenge.get("mac"), "l", peer.id, mine, nonce_d, nonce_l
            ):
                raise PeerError("refused")
            await ws.send(model.encode({
                "type": "proof", "mac": auth.mac(secret, "d", mine, peer.id, nonce_d, nonce_l),
            }))
            await self._recv(ws, "ready")
        except BaseException:
            await _close(ws)
            raise
        return ws

    async def _deliver(self, peer: Any) -> None:
        ws = await self._open_session(peer)
        try:
            await asyncio.to_thread(self.store.note_seen, peer.id, now_ms())
            await self._exchange(ws, peer.id, initiator=True)
        finally:
            await _close(ws)

    async def _exchange(self, ws: Any, peer_id: str, *, initiator: bool) -> None:
        """Le due code sulla stessa connessione, finché tutte e due hanno finito."""
        from websockets.exceptions import ConnectionClosed

        loop = asyncio.get_running_loop()
        waiting: dict[str, asyncio.Future[tuple[str, str]]] = {}
        remote_done = asyncio.Event()

        async def sender() -> None:
            lock = self._send_locks.setdefault(peer_id, asyncio.Lock())
            if not lock.locked():
                async with lock:
                    while True:
                        rows = await asyncio.to_thread(self.store.pending, peer_id)
                        if not rows:
                            break
                        for row in rows:
                            fut: asyncio.Future[tuple[str, str]] = loop.create_future()
                            waiting[row["id"]] = fut
                            await ws.send(model.encode({
                                "type": "msg", "id": row["id"], "kind": row["kind"],
                                "body": row["body"], "created_ms": row["created_ms"],
                            }))
                            verdict, reason = await asyncio.wait_for(fut, timeout=ACK_TIMEOUT_S)
                            waiting.pop(row["id"], None)
                            # Prima la riga in cronologia, poi via dalla coda:
                            # chi vede la coda vuota trova già detto perché.
                            if verdict == "nack":
                                await asyncio.to_thread(
                                    self.store.log, peer_id, "sys", "refused",
                                    {"kind": row["kind"], "reason": reason},
                                )
                            await asyncio.to_thread(self.store.remove, row["id"])
            await ws.send(model.encode({"type": "done"}))

        async def receiver() -> None:
            async for raw in ws:
                frame = model.decode(raw, "msg", "ack", "nack", "done")
                kind = frame["type"]
                if kind == "done":
                    remote_done.set()
                    if not initiator:
                        continue
                elif kind in ("ack", "nack"):
                    fut = waiting.get(frame.get("id"))  # type: ignore[arg-type]
                    if fut is not None and not fut.done():
                        fut.set_result((kind, text_field(frame, "reason", limit=60)))
                else:
                    await ws.send(model.encode(await self._receive(peer_id, frame)))

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        try:
            if initiator:
                await send_task
                await asyncio.wait_for(remote_done.wait(), timeout=DONE_TIMEOUT_S)
            else:
                done, _ = await asyncio.wait(
                    {send_task, recv_task}, return_when=asyncio.FIRST_EXCEPTION
                )
                for task in done:
                    if task.exception() is not None:
                        raise task.exception()  # type: ignore[misc]
                await recv_task
        except ConnectionClosed:
            if initiator:
                raise PeerError("closed") from None
        finally:
            for task in (send_task, recv_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(send_task, recv_task, return_exceptions=True)
            for fut in waiting.values():
                if not fut.done():
                    fut.cancel()

    async def _receive(self, peer_id: str, frame: dict[str, Any]) -> dict[str, Any]:
        msg_id = frame.get("id")
        kind = frame.get("kind")
        body = frame.get("body")
        if not is_id(msg_id) or not isinstance(kind, str) or not isinstance(body, dict):
            raise FrameError("bad msg")
        handler = self._handlers.get(kind)
        if handler is None:
            return {"type": "nack", "id": msg_id, "reason": "unsupported"}
        if not await asyncio.to_thread(self.store.first_arrival, peer_id, msg_id, now_ms()):
            return {"type": "ack", "id": msg_id}
        try:
            await handler(peer_id, body)
        except Exception:
            logger.opt(exception=True).warning("peers: handler for {} failed", kind)
            return {"type": "nack", "id": msg_id, "reason": "failed"}
        return {"type": "ack", "id": msg_id}

    # -- La coda ------------------------------------------------------------------------

    async def _pump(self) -> None:
        while True:
            try:
                wait = await self._next_wait()
                try:
                    await asyncio.wait_for(self._kick.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass
                self._kick.clear()
                await self._run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("peers: queue round failed")
                await asyncio.sleep(5)

    async def _next_wait(self) -> float:
        due = await asyncio.to_thread(self.store.next_due_ms)
        if due is None:
            return IDLE_CHECK_S
        return max(0.0, min(IDLE_CHECK_S, (due - now_ms()) / 1000))

    async def _run_due(self) -> None:
        cfg = await self._config()
        if not cfg.enabled:
            return
        known = {p.id: p for p in cfg.peers}
        for peer_id in await asyncio.to_thread(self.store.due_peers, now_ms()):
            peer = known.get(peer_id)
            if peer is None:
                await asyncio.to_thread(self.store.drop_peer, peer_id)
                continue
            wakes = self._wakes
            try:
                await self._deliver(peer)
            except Exception as exc:
                code = exc.code if isinstance(exc, PeerError) else type(exc).__name__
                now = now_ms()
                await asyncio.to_thread(self.store.postpone, peer_id, now)
                await asyncio.to_thread(self.store.note_failure, peer_id, now, code)
                logger.info("peers: {} not reached ({}), will retry", peer.name, code)
                # Una sveglia arrivata durante il tentativo vale ancora: il
                # rinvio appena scritto l'avrebbe coperta.
                if self._wakes != wakes:
                    self.wake(peer_id)

    # -- I gestori di questo passo ----------------------------------------------------------

    async def _on_ping(self, peer_id: str, body: dict[str, Any]) -> None:
        await asyncio.to_thread(self.store.log, peer_id, "in", KIND_PING, {})

    async def _on_unpair(self, peer_id: str, body: dict[str, Any]) -> None:
        logger.info("peers: {} unpaired us", peer_id[:8])
        # Dopo l'``ack``: chi ci scollega aspetta la risposta su questa
        # connessione, e il segreto serve ancora a chiuderla bene.
        task = asyncio.get_running_loop().create_task(self._forget_later(peer_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _forget_later(self, peer_id: str) -> None:
        await asyncio.sleep(1.0)
        try:
            await self._forget(peer_id)
            await asyncio.to_thread(self.store.log, peer_id, "sys", "removed_by_other", {})
        except Exception:
            logger.opt(exception=True).warning("peers: could not forget {}", peer_id[:8])


async def _close(ws: Any) -> None:
    try:
        await ws.close()
    except Exception:
        pass


__all__ = ["PeerError", "PeerLink", "clean_name"]
