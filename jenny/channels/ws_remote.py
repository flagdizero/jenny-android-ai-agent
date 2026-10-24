"""Il secondo ascoltatore del gateway: Jenny aperta da un altro dispositivo del tailnet.

Il gateway ascolta su ``127.0.0.1``: lo raggiunge solo la WebView del telefono.
Con ``gateway.remoteAccess.enabled`` acceso nasce un secondo server, sullo
stesso handler e sulla stessa porta, legato agli indirizzi **Tailscale** del
telefono. Il primo non si tocca mai: accendere, spegnere o perdere Tailscale non
può staccare la WebView.

Tre scelte, ognuna contro un modo preciso di sbagliare:

- **Si lega agli indirizzi del tailnet, non a ``0.0.0.0``.** Su Linux un socket
  in ascolto su ``0.0.0.0:18790`` urta quello già su ``127.0.0.1:18790``; e
  soprattutto sulla Wi-Fi di casa il link — che porta la chiave — viaggerebbe in
  chiaro. Gli indirizzi si rileggono ogni ``POLL_S`` secondi: al boot Tailscale
  può non essere ancora su, e se cade e torna l'ascoltatore lo segue da solo.
- **Ogni connessione passa da** :func:`peer_allowed` **prima di qualunque
  altra cosa**: l'indirizzo locale dev'essere uno di quelli legati, e il peer
  del tailnet. Linux accetta un pacchetto per un suo indirizzo da qualunque
  interfaccia (weak host model), quindi un vicino di Wi-Fi che mandasse a
  ``100.x`` arriverebbe fin qui: il suo indirizzo, però, non è del tailnet.
- **La chiave è un'altra.** Su questo ascoltatore vale solo
  ``gateway.remoteAccess.key`` (v. :func:`auth_secret`), mai il segreto della
  WebView: cambiarla scollega gli altri dispositivi e lascia il telefono com'è.

La rete restringe chi può provarci; il permesso resta la chiave.

**Si ripara da solo.** Misurato sul Titan il 24/10/2026: ogni volta che
Tailscale ristabilisce la VPN (un off/on, una netmap nuova, un cambio di rete)
Android distrugge i socket legati agli indirizzi di ``tun0``, anche quelli in
ascolto. Gli indirizzi tornano identici, quindi rileggerli non basta: un
confronto «stessi indirizzi, server c'è» lasciava un ascoltatore morto per
sempre, con asyncio che girava a vuoto su ``accept()`` → ``EINVAL`` (74% di CPU,
160 mila tracebacks nel log). Ora ogni giro controlla che i socket siano
ancora in ascolto (:func:`server_listening`), e il primo ``accept`` fallito
sveglia subito il giro invece di aspettarlo (:meth:`RemoteListener.kick`).
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from loguru import logger

# Gli indirizzi che Tailscale assegna ai nodi del tailnet. Il /10 è anche il
# CGNAT degli operatori: da solo non basta a dire «Tailscale», e infatti la
# rilevazione pretende l'indirizzo IPv6, che è solo suo (v. `detect_tailscale`).
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")

# Il resolver MagicDNS di Tailscale, nelle due famiglie. Non gli si manda niente:
# un `connect` UDP sceglie solo la rotta, e l'indirizzo sorgente che il kernel
# sceglierebbe è quello del telefono sul tailnet — se Tailscale è su.
_PROBE_V4 = "100.100.100.100"
_PROBE_V6 = "fd7a:115c:a1e0::53"

# Ogni quanto si rileggono gli indirizzi. Dieci secondi: Tailscale che torna su
# dopo un riavvio dell'app è aperto da fuori entro un battito.
POLL_S = 10.0

# Dopo un bind fallito con Tailscale su, si riprova presto: succede quando i
# socket sono appena stati distrutti e gli indirizzi non sono ancora tornati su
# ``tun0`` (``EADDRNOTAVAIL``), e dieci secondi lì sono dieci secondi senza Jenny.
_RETRY_S = 2.0

# Quanto si aspetta un nome MagicDNS. È un di più: senza, si mostra l'indirizzo.
_NAME_TIMEOUT_S = 2.0

# Tetti ai due punti in cui il giro aspetta qualcun altro. Un giro bloccato
# tiene il lock, e col lock preso nessuna riparazione successiva può partire:
# meglio un giro saltato, o una chiusura lasciata a metà, che un ascoltatore
# fermo per sempre.
_DETECT_TIMEOUT_S = 5.0
_CLOSE_TIMEOUT_S = 5.0

# Tailscale giù con l'opzione accesa: Jenny lo riaccende (``runtime.tailscale``).
# Dopo due giri vuoti di fila (~20 s: un off/on a mano non fa scattare niente),
# poi a pause crescenti fino a dieci minuti, finché non torna. I primi due
# tentativi sono il solo messaggio, invisibile; dal terzo si apre anche l'app,
# che si vede ma è l'unico gesto misurato che lo riporta su dopo un kill.
_REVIVE_AFTER_ROUNDS = 2
_REVIVE_BACKOFF_S = (30.0, 60.0, 120.0, 300.0, 600.0)
_BROADCASTS_BEFORE_APP = 2
# Dopo un tentativo i giri si fanno fitti per un po': il tunnel torna in pochi
# secondi, e ascoltare subito vale più di un giro risparmiato.
_FAST_AFTER_REVIVE_S = 30.0

ReviveFn = Callable[..., Awaitable[Any]]

# Su questo ascoltatore: la chiave che vale (``None`` = ascoltatore della WebView).
# Una ContextVar perché i controlli della chiave stanno in posti che la
# connessione non la vedono (``GatewayHTTPHandler``), e il task di una
# connessione è uno solo dall'handshake alla chiusura: impostata nel suo
# ``process_request``, vale per tutto quel che segue.
_REMOTE_KEY: ContextVar[str | None] = ContextVar("jenny_remote_key", default=None)


def auth_secret(master: str) -> str:
    """La chiave da confrontare: quella remota sull'ascoltatore Tailscale, altrimenti *master*."""
    key = _REMOTE_KEY.get()
    return master if key is None else key


def on_remote_listener() -> bool:
    """Vero dentro una richiesta arrivata dall'ascoltatore Tailscale."""
    return _REMOTE_KEY.get() is not None


def _host_of(address: Any) -> str | None:
    if isinstance(address, tuple) and address:
        address = address[0]
    return address if isinstance(address, str) else None


def parse_ip(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Un indirizzo come lo dà un socket, ricondotto alla sua forma vera.

    Toglie lo zone id (``fe80::1%wlan0``) e scioglie le forme IPv4-mapped
    (``::ffff:100.64.0.1``): senza, il peer di un socket doppio-stack sembrerebbe
    IPv6 e non cadrebbe mai nel /10.
    """
    if not host:
        return None
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_tailnet_ip(host: str | None) -> bool:
    ip = parse_ip(host)
    if ip is None:
        return False
    return ip in (TAILNET_V4 if ip.version == 4 else TAILNET_V6)


def peer_allowed(local: Any, peer: Any, bound: tuple[str, ...]) -> bool:
    """La connessione è arrivata su un indirizzo legato, da un nodo del tailnet."""
    local_ip = parse_ip(_host_of(local))
    if local_ip is None or local_ip not in {parse_ip(b) for b in bound}:
        return False
    return is_tailnet_ip(_host_of(peer))


def _source_address(family: int, probe: str) -> str | None:
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((probe, 53))
            return s.getsockname()[0]
    except OSError:
        return None


def detect_tailscale() -> tuple[str, ...]:
    """Gli indirizzi del telefono sul tailnet, o ``()`` se Tailscale non è su.

    Prima l'IPv6, che è solo di Tailscale: se la rotta verso il suo resolver non
    parte da un ``fd7a:115c:a1e0::/48``, Tailscale non c'è, e l'IPv4 non si
    guarda nemmeno — su rete mobile l'indirizzo dell'operatore può essere un
    ``100.x`` qualunque. Bloccante (due socket): va chiamata in un thread.
    """
    v6 = _source_address(socket.AF_INET6, _PROBE_V6)
    if not is_tailnet_ip(v6):
        return ()
    assert v6 is not None
    v4 = _source_address(socket.AF_INET, _PROBE_V4)
    return (v4, v6) if v4 is not None and is_tailnet_ip(v4) else (v6,)


def server_listening(server: Any) -> bool:
    """Vero se ogni socket del server è ancora in ascolto.

    Un socket distrutto da Android resta un descrittore aperto e registrato nel
    loop, ma non è più in ``LISTEN``: ``SO_ACCEPTCONN`` lo dice senza toccarlo.
    Un server che non espone ``sockets`` (i finti delle prove) si crede vivo; uno
    che ne espone zero è chiuso.
    """
    sockets = getattr(server, "sockets", None)
    if sockets is None:
        return True
    if not sockets:
        return False
    acceptconn = getattr(socket, "SO_ACCEPTCONN", None)
    for sock in sockets:
        try:
            if sock.fileno() == -1:
                return False
            if acceptconn is not None and not sock.getsockopt(socket.SOL_SOCKET, acceptconn):
                return False
        except OSError as exc:
            # macOS non risponde a ``SO_ACCEPTCONN`` (``ENOPROTOOPT``): lì non
            # si sa, e un «non so» non deve far rifare un server sano a ogni
            # giro. Linux e Android rispondono; ogni altro errore è un socket rotto.
            if exc.errno == errno.ENOPROTOOPT:
                continue
            return False
    return True


async def write_remote_access(*, enabled: bool | None = None, rotate: bool = False) -> None:
    """Scrive ``gateway.remoteAccess`` attraverso ``store.mutate``.

    Accendere la prima volta genera la chiave; ``rotate`` ne mette una nuova
    (le connessioni con la vecchia le chiude ``RemoteListener.apply``).
    Spegnere la tiene: riaccendendo, il link salvato sul Mac torna buono.
    """
    import secrets

    from jenny.config import store

    def _apply(config: Any) -> bool:
        remote = config.gateway.remote_access
        before = (remote.enabled, remote.key)
        if enabled is not None:
            remote.enabled = enabled
        if rotate or (remote.enabled and not remote.key):
            remote.key = secrets.token_urlsafe(32)
        return (remote.enabled, remote.key) != before

    await store.mutate(_apply)


def _lookup_name(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0].rstrip(".") or None
    except (OSError, UnicodeError):
        return None


@dataclass
class _Peer:
    ip: str
    since: float
    name: str | None = None


ServeFn = Callable[[tuple[str, ...]], Awaitable[Any]]


class RemoteListener:
    """Il secondo server: lo accende, lo spegne e lo tiene legato a Tailscale.

    Non sa niente di WebSocket: il server lo costruisce chi lo possiede
    (``serve``, che riceve gli indirizzi su cui legarsi). Qui c'è lo stato —
    acceso o no, legato dove, chi è collegato — e il giro che segue Tailscale.
    """

    def __init__(
        self,
        *,
        serve: ServeFn,
        port: int,
        detect: Callable[[], tuple[str, ...]] = detect_tailscale,
        lookup_name: Callable[[str], str | None] = _lookup_name,
        poll_s: float = POLL_S,
        revive: ReviveFn | None = None,
    ) -> None:
        self._serve = serve
        self.port = port
        self._detect = detect
        self._lookup_name = lookup_name
        self._poll_s = poll_s
        # ``revive(open_app=...)``: riaccende Tailscale. ``None`` = non ci prova
        # (le prove, e chiunque usi l'ascoltatore fuori dal telefono).
        self._revive = revive
        self._down_rounds = 0
        self._down_since: float | None = None
        self._revive_attempts = 0
        self._next_revive_at = 0.0
        self._fast_until = 0.0
        self._revive_task: asyncio.Task[Any] | None = None
        self._enabled = False
        self._key = ""
        self._bound: tuple[str, ...] = ()
        self._server: Any | None = None
        self._retiring: Any | None = None
        self._watch: asyncio.Task[None] | None = None
        self._error: str | None = None
        self._peers: dict[Any, _Peer] = {}
        self._names: dict[str, str | None] = {}
        self._lookups: set[asyncio.Task[Any]] = set()
        # Uno scambio alla volta: il giro e un `set` che arrivano insieme
        # chiuderebbero e riaprirebbero lo stesso server due volte.
        self._lock = asyncio.Lock()
        # Sveglia il giro prima della scadenza: la tira il primo ``accept``
        # fallito (v. ``_on_loop_exception``).
        self._kick = asyncio.Event()
        # Il prossimo giro arriva presto: un bind è appena fallito con Tailscale su.
        self._retry_soon = False
        # Un solo avviso per episodio di socket distrutti, non uno per giro a vuoto.
        self._spin_reported = False
        self._last_rebind: dict[str, Any] | None = None
        # Il gestore delle eccezioni del loop, finché l'opzione è accesa.
        self._guard: Callable[[asyncio.AbstractEventLoop, dict[str, Any]], None] | None = None
        self._guard_loop: asyncio.AbstractEventLoop | None = None
        self._guard_previous: Any = None

    # -- Stato --------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def key(self) -> str:
        return self._key

    @property
    def bound(self) -> tuple[str, ...]:
        return self._bound

    def is_remote(self, connection: Any) -> bool:
        return connection in self._peers

    def admit(self, connection: Any) -> str | None:
        """La chiave per questa richiesta, o ``None`` se va rifiutata.

        La chiamata del ``process_request`` del secondo server, prima di
        qualunque altra cosa. Imposta anche la ContextVar di :func:`auth_secret`.
        """
        local = getattr(connection, "local_address", None)
        peer = getattr(connection, "remote_address", None)
        if not self._enabled or not self._key or not peer_allowed(local, peer, self._bound):
            logger.info("remote access: refused {} on {}", _host_of(peer), _host_of(local))
            return None
        _REMOTE_KEY.set(self._key)
        return self._key

    def track(self, connection: Any) -> None:
        ip = _host_of(getattr(connection, "remote_address", None)) or "?"
        self._peers[connection] = _Peer(ip=ip, since=time.time())
        # Il nome si chiede subito, in disparte: quando arriva il primo
        # messaggio da questa connessione ``origin_of`` lo trova gia' pronto,
        # invece di far aspettare il turno al DNS.
        if ip not in self._names:
            task = asyncio.get_running_loop().create_task(self._name_of(ip))
            self._lookups.add(task)
            task.add_done_callback(self._lookups.discard)

    def origin_of(self, connection: Any) -> dict[str, str] | None:
        """Da dove arriva un messaggio: ``None`` se non da fuori.

        ``{"origin": "remote", "device": "mac"}`` per una connessione
        dell'ascoltatore Tailscale; ``device`` c'e' solo se MagicDNS ha dato un
        nome. Non aspetta niente: legge il nome gia' chiesto da ``track``.
        """
        peer = self._peers.get(connection)
        if peer is None:
            return None
        origin = {"origin": "remote"}
        name = self._names.get(peer.ip)
        if name:
            origin["device"] = name.split(".", 1)[0]
        return origin

    def forget(self, connection: Any) -> None:
        self._peers.pop(connection, None)

    async def status(self) -> dict[str, Any]:
        """Quel che il pannello dell'officina mostra.

        ``name`` è il nome MagicDNS del telefono, se il resolver lo dà: l'indirizzo
        da aprire è più facile da leggere, e regge un cambio d'IP.
        """
        # A opzione spenta non c'è niente di legato, ma il pannello dice lo
        # stesso se Tailscale c'è: è la prima cosa che serve sapere per accenderla.
        found = self._bound or await self._detect_now() or ()
        # L'IPv4 per primo: è quello che si legge e si detta.
        host = min(found, key=lambda a: ":" in a, default=None)
        name = await self._name_of(host) if host else None
        # Un dispositivo, una riga: una scheda del browser apre piu' di un
        # WebSocket, e la stessa persona con due finestre sul Mac non e' «tre
        # collegati». ``windows`` dice quante ne tiene aperte.
        by_ip: dict[str, dict[str, Any]] = {}
        for p in sorted(self._peers.values(), key=lambda p: p.since):
            if p.ip in by_ip:
                by_ip[p.ip]["windows"] += 1
                continue
            peer_name = await self._name_of(p.ip)
            by_ip[p.ip] = {
                "ip": p.ip,
                "name": peer_name.split(".", 1)[0] if peer_name else None,
                "since": p.since,
                "windows": 1,
            }
        peers = list(by_ip.values())
        if found:
            tailscale = "up"
        elif self._enabled and self._revive_attempts:
            tailscale = "reviving"
        else:
            tailscale = "down"
        return {
            "enabled": self._enabled,
            "listening": self._server is not None,
            "tailscale": tailscale,
            "revive_attempts": self._revive_attempts,
            "address": host,
            "name": name,
            "port": self.port,
            "error": self._error,
            "peers": peers,
            "last_rebind": self._last_rebind,
        }

    async def _name_of(self, ip: str) -> str | None:
        if ip not in self._names:
            try:
                self._names[ip] = await asyncio.wait_for(
                    asyncio.to_thread(self._lookup_name, ip), timeout=_NAME_TIMEOUT_S
                )
            except (asyncio.TimeoutError, OSError):
                self._names[ip] = None
        return self._names[ip]

    # -- Ciclo di vita ------------------------------------------------------

    async def apply(self, *, enabled: bool, key: str) -> None:
        """Porta l'ascoltatore allo stato di config. Idempotente.

        Una chiave nuova chiude le connessioni aperte con la vecchia: è il
        «scollega tutti», e vale anche quando l'opzione resta accesa.
        """
        rotated = bool(self._key) and key != self._key
        self._enabled = enabled and bool(key)
        self._key = key
        if rotated:
            await self.disconnect_all()
        # ``done()``: un giro morto non va lasciato al suo posto. Senza, un
        # ``apply`` successivo lo trovava «c'è» e non ne faceva partire un altro.
        if self._enabled and (self._watch is None or self._watch.done()):
            self._install_guard()
            await self._sync()
            self._start_watch()
        elif not self._enabled:
            await self.stop()

    def kick(self) -> None:
        """Fa partire subito il prossimo giro, senza aspettare ``POLL_S``."""
        self._kick.set()

    async def stop(self) -> None:
        self._enabled = False
        watch, self._watch = self._watch, None
        if watch is not None:
            watch.cancel()
            try:
                await watch
            except asyncio.CancelledError:
                pass
        revive, self._revive_task = self._revive_task, None
        if revive is not None and not revive.done():
            revive.cancel()
            await asyncio.gather(revive, return_exceptions=True)
        self._down_rounds = self._revive_attempts = 0
        self._down_since = None
        self._next_revive_at = self._fast_until = 0.0
        async with self._lock:
            await self._close()
        self._remove_guard()

    async def disconnect_all(self) -> None:
        conns = list(self._peers)
        self._peers.clear()
        for conn in conns:
            try:
                await conn.close(code=1008, reason="remote access key changed")
            except Exception:
                logger.opt(exception=True).debug("remote access: close failed")

    # -- Il giro ------------------------------------------------------------

    def _start_watch(self) -> None:
        task = asyncio.create_task(self._watch_loop(), name="remote-access-watch")
        task.add_done_callback(self._on_watch_done)
        self._watch = task

    def _on_watch_done(self, task: asyncio.Task[None]) -> None:
        """Un giro morto per un'eccezione, con l'opzione accesa, riparte.

        Il corpo del giro le prende già tutte: questa è la rete sotto la rete,
        perché un giro fermo è un ascoltatore che non si ripara più.
        """
        if task is not self._watch or task.cancelled() or not self._enabled:
            return
        exc = task.exception()
        if not isinstance(exc, Exception):
            return
        logger.opt(exception=exc).error("remote access: watch died, restarting it")
        self._start_watch()

    async def _watch_loop(self) -> None:
        while self._enabled:
            fast = self._retry_soon or time.monotonic() < self._fast_until
            wait = min(_RETRY_S, self._poll_s) if fast else self._poll_s
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            self._kick.clear()
            try:
                await self._sync()
            except Exception:
                logger.opt(exception=True).warning("remote access: sync failed")

    async def _detect_now(self) -> tuple[str, ...] | None:
        """Gli indirizzi del tailnet, o ``None`` se la rilevazione non risponde."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._detect), timeout=_DETECT_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            logger.warning("remote access: Tailscale detection timed out")
            return None

    async def _sync(self) -> None:
        found = await self._detect_now()
        if found is None:
            # Un giro saltato, non «Tailscale è giù»: chiudere un ascoltatore
            # sano perché la rilevazione ha tardato sarebbe il guasto, non la cura.
            return
        if self._enabled:
            self._follow_tailscale(found)
        async with self._lock:
            if not self._enabled:
                return
            alive = self._server is not None and server_listening(self._server)
            if found == self._bound and (alive or not found):
                self._retry_soon = False
                return
            if self._server is not None and found == self._bound:
                reason = "sockets destroyed"
            elif self._server is not None:
                reason = "addresses changed"
            else:
                reason = "Tailscale up"
            await self._close()
            if not found:
                self._retry_soon = False
                logger.info("remote access: Tailscale is down, not listening")
                return
            try:
                self._server = await self._serve(found)
            except OSError as exc:
                self._error = exc.strerror or str(exc)
                self._retry_soon = True
                logger.warning("remote access: cannot listen on {}: {}", found, self._error)
                return
            self._bound = found
            self._error = None
            self._retry_soon = False
            self._spin_reported = False
            self._last_rebind = {"at": time.time(), "reason": reason}
            logger.info(
                "remote access: listening on {} port {} ({})", ", ".join(found), self.port, reason
            )

    # -- Tailscale giù: riaccenderlo ----------------------------------------

    def _follow_tailscale(self, found: tuple[str, ...]) -> None:
        """Conta i giri senza Tailscale e, quando è ora, lo fa riaccendere.

        Il gesto parte in un suo task: il giro non aspetta l'app di Tailscale
        (fino a qualche secondo) e il lock resta libero.
        """
        now = time.monotonic()
        if found:
            if self._down_since is not None and self._revive_attempts:
                logger.info(
                    "remote access: Tailscale back after {:.0f}s ({} attempts)",
                    now - self._down_since,
                    self._revive_attempts,
                )
            self._down_rounds = 0
            self._down_since = None
            self._revive_attempts = 0
            self._next_revive_at = 0.0
            return
        if self._down_since is None:
            self._down_since = now
        self._down_rounds += 1
        if self._revive is None or self._down_rounds < _REVIVE_AFTER_ROUNDS:
            return
        if now < self._next_revive_at:
            return
        if self._revive_task is not None and not self._revive_task.done():
            return
        attempt = self._revive_attempts
        self._revive_attempts += 1
        self._next_revive_at = now + _REVIVE_BACKOFF_S[min(attempt, len(_REVIVE_BACKOFF_S) - 1)]
        self._fast_until = now + _FAST_AFTER_REVIVE_S
        open_app = attempt >= _BROADCASTS_BEFORE_APP
        logger.warning(
            "remote access: Tailscale down for {:.0f}s, asking it to reconnect (attempt {}{})",
            now - self._down_since,
            attempt + 1,
            ", opening the app" if open_app else "",
        )
        self._revive_task = asyncio.get_running_loop().create_task(
            self._run_revive(open_app), name="remote-access-revive"
        )

    async def _run_revive(self, open_app: bool) -> None:
        assert self._revive is not None
        try:
            await self._revive(open_app=open_app)
        except Exception:
            logger.opt(exception=True).warning("remote access: reviving Tailscale failed")
        self.kick()

    async def _close(self) -> None:
        server, self._server = self._server, None
        self._bound = ()
        self._peers.clear()
        if server is not None:
            # ``websockets`` chiude il server asyncio in un suo task: fino a lì
            # i socket distrutti continuano a fallire, e restano nostri.
            self._retiring = server
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=_CLOSE_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("remote access: server did not close in time, moving on")
            finally:
                self._retiring = None

    # -- Socket distrutti: la sveglia ---------------------------------------

    def _install_guard(self) -> None:
        """Mette il gestore delle eccezioni del loop che riconosce ``accept`` fallito.

        Quando Android distrugge un socket in ascolto, asyncio lo trova pronto,
        ``accept()`` dà ``EINVAL`` e l'eccezione finisce qui a ogni giro del
        loop. Il gestore sveglia il giro di riparazione e, se il socket è nostro,
        tace le ripetizioni. Tutto il resto passa al gestore di prima.
        """
        loop = asyncio.get_running_loop()
        if self._guard_loop is loop and loop.get_exception_handler() is self._guard:
            return
        previous = loop.get_exception_handler()

        def guard(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            self._on_loop_exception(loop, context, previous)

        loop.set_exception_handler(guard)
        self._guard, self._guard_loop, self._guard_previous = guard, loop, previous

    def _remove_guard(self) -> None:
        loop, guard, previous = self._guard_loop, self._guard, self._guard_previous
        self._guard = self._guard_loop = self._guard_previous = None
        # Solo se è ancora il nostro: chi ne ha messo uno dopo non va scavalcato.
        if loop is None or loop.is_closed() or loop.get_exception_handler() is not guard:
            return
        loop.set_exception_handler(previous)

    def _on_loop_exception(
        self, loop: asyncio.AbstractEventLoop, context: dict[str, Any], previous: Any,
    ) -> None:
        exc = context.get("exception")
        handle = context.get("handle")
        # Il nome del callback si legge dal repr dell'handle: se una versione
        # futura di asyncio lo cambia, la sveglia tace e ripara il giro ogni
        # ``POLL_S`` (``server_listening``), solo più tardi.
        if isinstance(exc, OSError) and handle is not None and "_accept_connection" in repr(handle):
            self.kick()
            if self._owns(handle):
                if not self._spin_reported:
                    self._spin_reported = True
                    logger.warning("remote access: listening sockets failed ({}), rebinding", exc)
                return
        if previous is not None:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    def _owns(self, handle: Any) -> bool:
        """L'``accept`` fallito è su uno dei nostri socket (anche di quello che si sta chiudendo)?"""
        servers = [s for s in (self._server, self._retiring) if s is not None]
        ours: set[int] = set()
        inners: list[Any] = []
        for server in servers:
            for sock in getattr(server, "sockets", None) or ():
                try:
                    ours.add(sock.fileno())
                except OSError:
                    pass
            inner = getattr(server, "server", None)
            if inner is not None:
                inners.append(inner)
        for arg in getattr(handle, "_args", None) or ():
            if any(arg is inner for inner in inners):
                return True
            if isinstance(arg, socket.socket) and arg.fileno() in ours:
                return True
        return False
