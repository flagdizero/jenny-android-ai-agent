"""Reverse proxy su loopback per la "vista esterna" di una Jenny App.

Perche' non e' una route del gateway
------------------------------------
Il gateway HTTP gira sul parser di ``websockets`` (``websockets.http11.Request``),
che legge riga di richiesta e header e **non il body**: e' la stessa ragione per
cui ``apps/http.py`` documenta la tratta browser→gateway come GET-only. Una UI
remota reale fa POST, quindi montarla come route del gateway non era possibile.

Secondo motivo, indipendente: sotto un prefisso di path i path assoluti della
pagina remota (``/style.css``, ``/api/x``) si risolverebbero sulla radice del
gateway. Servirebbe riscrittura degli URL, che e' fragile per definizione. Un
listener con **origine propria** li fa funzionare senza toccare un byte.

Perche' su loopback
-------------------
La policy di rete dell'APK (``network_security_config.xml``) permette il
cleartext **solo** verso ``127.0.0.1``/``localhost``: una WebView che incornicia
un ``http://`` non-loopback prende ``ERR_CLEARTEXT_NOT_PERMITTED`` prima di
aprire un socket. Passare da loopback rende la policy inapplicabile al caso,
senza allargarla e senza mettere il nome di un host personale nell'APK.

Il proxy e' trasparente a livello di byte: riscrive la prima riga e l'``Host``,
poi ristreamma. Quindi ogni metodo, i body, il chunked e l'upgrade WebSocket
passano senza casi speciali.

**Una richiesta per connessione.** Riscrivere vale solo per la testa che si e'
letta: su una connessione keep-alive la seconda richiesta passava come byte
grezzi, con il cookie-capability e l'``Host`` del proxy, al server dell'utente.
Quindi al server si chiede ``Connection: close``, al browser si risponde
``Connection: close`` (non riusa la connessione) e dopo il body dichiarato della
prima richiesta non si inoltra piu' niente di quel che il client manda. Fanno
eccezione l'upgrade WebSocket, che dopo il 101 non e' piu' HTTP, e un body
``chunked``, che senza un parser non si delimita (i browser non ne mandano).

Superficie e cosa la limita
---------------------------
- bind su ``127.0.0.1`` e porta effimera, mai su un'interfaccia esterna;
- **un solo upstream fisso**, validato una volta con
  ``validate_app_server_target``: il proxy non e' pilotabile verso un altro
  indirizzo, e non segue redirect a livello proprio (li ristreamma al browser,
  che li risolve sull'origine del proxy);
- **capability**: la prima navigazione deve presentare un segreto da 128 bit nel
  path; il proxy risponde 302 e lo sposta in un cookie, cosi' i path assoluti
  della pagina remota continuano a funzionare. Senza cookie ne' prefisso: 403.
  Serve perche' ``127.0.0.1:<porta>`` e' raggiungibile da qualunque app del
  telefono, e una porta effimera si scandaglia in pochi secondi.
- vive quanto la vista: ``close()`` alla chiusura dell'app, piu' un idle timeout.

Limite noto della capability: i cookie sono per-host e **ignorano la porta**,
quindi il cookie viaggia verso qualunque server su ``127.0.0.1`` che la WebView
contatti. In pratica sono solo questo proxy e il gateway (entrambi nostri), ma e'
il motivo per cui il valore viene **rimosso** dalla richiesta inoltrata: il
server dell'utente non deve vederlo.
"""

from __future__ import annotations

import asyncio
import secrets
from urllib.parse import urlsplit

from loguru import logger

from jenny.security.network import validate_app_server_target

COOKIE_NAME = "jenny_app_view"
MAX_HEADER_BYTES = 64 * 1024
DEFAULT_IDLE_TIMEOUT_S = 30 * 60.0
_CRLF2 = b"\r\n\r\n"


class AppViewProxyError(Exception):
    """Avvio impossibile: target rifiutato o non interpretabile."""


def _split_upstream(base_url: str) -> tuple[str, int, str]:
    """(host, port, prefisso di path) da un ``server.baseUrl``."""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise AppViewProxyError(f"unsupported scheme: {parts.scheme!r}")
    if parts.scheme == "https":
        # Il proxy e' un tunnel di byte in chiaro: verso un upstream TLS
        # dovrebbe terminare la connessione, che e' un altro sottosistema. Un
        # server https non ha comunque bisogno di questo proxy — la policy
        # cleartext non lo tocca, l'iframe lo carica direttamente.
        raise AppViewProxyError(
            "https app servers do not need the view proxy: frame the URL directly"
        )
    if not parts.hostname:
        raise AppViewProxyError("baseUrl has no host")
    return parts.hostname, parts.port or 80, parts.path.rstrip("/")


class AppViewProxy:
    """Un listener su loopback che inoltra a un solo server d'app."""

    def __init__(self, slug: str, base_url: str, *, idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S):
        self.slug = slug
        self.base_url = base_url
        self._host, self._port, self._prefix = _split_upstream(base_url)
        self._cap = secrets.token_urlsafe(16)
        self._idle_timeout_s = idle_timeout_s
        self._server: asyncio.AbstractServer | None = None
        self._bound_port: int | None = None
        self._conns: set[asyncio.Task] = set()
        self._idle_task: asyncio.Task | None = None
        self._last_activity = 0.0

    # ── ciclo di vita ────────────────────────────────────────────────────

    async def start(self) -> str:
        """Valida il target, apre il listener, torna l'URL d'ingresso."""
        ok, error = await asyncio.to_thread(validate_app_server_target, self.base_url)  # DNS fuori dal loop
        if not ok:
            raise AppViewProxyError(f"blocked server target: {error}")

        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self._bound_port = self._server.sockets[0].getsockname()[1]
        self._touch()
        self._idle_task = asyncio.create_task(self._reap_when_idle())
        logger.info(
            "App '{}' view proxy on 127.0.0.1:{} -> {}",
            self.slug, self._bound_port, self.base_url,
        )
        return self.entry_url

    @property
    def entry_url(self) -> str:
        return f"http://127.0.0.1:{self._bound_port}/{self._cap}/"

    @property
    def port(self) -> int | None:
        return self._bound_port

    @property
    def closed(self) -> bool:
        """Il listener non accetta piu': chiuso dalla UI o dall'idle timeout.

        Chi tiene un registro dei proxy lo deve chiedere prima di riusarne uno:
        il reaper chiude da se', senza avvisare nessuno, e un ``entry_url``
        riusato dopo punta a una porta morta (un iframe vuoto).
        """
        return self._server is None

    async def close(self) -> None:
        """Chiude il listener. L'ORDINE dei quattro passi non e' arbitrario.

        ``wait_closed()`` attende che finiscano anche i **gestori** delle
        connessioni ancora aperte (da 3.12; su 3.11 ritorna subito). Un gestore
        del proxy sta normalmente appeso su ``read()`` in attesa del prossimo
        byte, quindi aspettarlo *prima* di cancellarlo e' un deadlock: la
        chiusura non ritornava mai. Ed e' il caso normale, non un caso limite —
        l'utente chiude l'app con la pagina caricata e la connessione viva, e la
        route ``.../view/close`` restava appesa.

        Quindi: prima si smette di accettare, poi si cancellano i gestori, e solo
        allora si aspetta.
        """
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for task in list(self._conns):
            task.cancel()
        self._conns.clear()
        if server is not None:
            try:
                await server.wait_closed()
            except Exception:  # pragma: no cover - chiusura best-effort
                pass
        logger.info("App '{}' view proxy closed", self.slug)

    def _touch(self) -> None:
        self._last_activity = asyncio.get_running_loop().time()

    async def _reap_when_idle(self) -> None:
        """Chiude il listener dopo un periodo senza traffico.

        Rete di sicurezza, non il meccanismo principale: la chiusura normale la
        fa la UI quando l'utente chiude l'app. Serve per il caso in cui quel
        segnale non arrivi (processo della WebUI ucciso, overlay perso), che
        altrimenti lascerebbe una porta aperta a tempo indefinito.
        """
        try:
            while True:
                loop = asyncio.get_running_loop()
                idle = loop.time() - self._last_activity
                remaining = self._idle_timeout_s - idle
                if remaining <= 0:
                    logger.info(
                        "App '{}' view proxy idle for {:.0f}s, closing", self.slug, idle
                    )
                    # Sganciare il riferimento prima di close(), altrimenti
                    # close() cancella il task corrente — cioe' questo.
                    #
                    # **Difensivo, non load-bearing**: con l'ordine dei passi in
                    # close() l'unico await avviene dopo la cancellazione dei
                    # gestori e ritorna subito, quindi l'auto-cancellazione non
                    # trova un punto di sospensione dove scattare. Nessun test
                    # lo raggiunge (provato: rimuovendo questa riga la suite
                    # resta verde). Serve perche' basta aggiungere un await
                    # dentro close() perche' torni a mordere.
                    self._idle_task = None
                    await self.close()
                    return
                await asyncio.sleep(min(remaining, 60.0))
        except asyncio.CancelledError:
            raise

    # ── una connessione ──────────────────────────────────────────────────

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._conns.add(task)
        try:
            self._touch()
            await self._serve_one(reader, writer)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as exc:
            logger.debug("App '{}' view proxy connection error: {}", self.slug, exc)
        finally:
            if task is not None:
                self._conns.discard(task)
            _close_quietly(writer)

    async def _serve_one(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head, rest = await self._read_head(reader)
        if head is None:
            return
        try:
            request_line, header_lines = _parse_head(head)
        except ValueError:
            await _respond(writer, 400, "Bad Request")
            return

        method, path, version = request_line
        authorized, redirect_to = self._authorize(path, header_lines)
        if redirect_to is not None:
            # Sposta la capability dal path a un cookie e rimanda alla radice:
            # da qui in avanti i path assoluti della pagina remota funzionano.
            await _respond(
                writer, 302, "Found",
                extra=[
                    ("Location", redirect_to),
                    ("Set-Cookie", f"{COOKIE_NAME}={self._cap}; Path=/; HttpOnly; SameSite=Lax"),
                ],
            )
            return
        if not authorized:
            await _respond(writer, 403, "Forbidden")
            return

        upstream_path = self._prefix + path if self._prefix else path
        upgrade = _is_upgrade(header_lines)
        out_head = _rebuild_head(
            method, upstream_path, version, header_lines,
            host=f"{self._host}:{self._port}" if self._port != 80 else self._host,
            upgrade=upgrade,
        )
        body_length = None if upgrade else _body_length(header_lines)

        try:
            up_reader, up_writer = await asyncio.open_connection(self._host, self._port)
        except OSError as exc:
            logger.debug("App '{}' view proxy upstream unreachable: {}", self.slug, exc)
            await _respond(writer, 502, "Bad Gateway")
            return

        try:
            if upgrade:
                up_writer.write(out_head + rest)
                await up_writer.drain()
                await asyncio.gather(
                    self._pump(up_reader, writer),
                    self._pump(reader, up_writer),
                )
            elif body_length is None:
                # ``chunked``: il body non si delimita senza un parser.
                up_writer.write(out_head + rest)
                await up_writer.drain()
                await asyncio.gather(
                    self._pump_response(up_reader, writer),
                    self._pump(reader, up_writer),
                )
            else:
                first = rest[:body_length]
                up_writer.write(out_head + first)
                await up_writer.drain()
                await asyncio.gather(
                    self._pump_response(up_reader, writer),
                    self._forward_body_then_drain(reader, up_writer, body_length - len(first)),
                )
        finally:
            _close_quietly(up_writer)

    async def _read_head(
        self, reader: asyncio.StreamReader
    ) -> tuple[bytes | None, bytes]:
        """Legge fino alla fine degli header; torna (head, byte già letti oltre)."""
        buf = bytearray()
        while _CRLF2 not in buf:
            if len(buf) > MAX_HEADER_BYTES:
                return None, b""
            chunk = await reader.read(4096)
            if not chunk:
                return None, b""
            buf.extend(chunk)
        head, _, rest = bytes(buf).partition(_CRLF2)
        return head, rest

    def _authorize(
        self, path: str, header_lines: list[tuple[str, str]]
    ) -> tuple[bool, str | None]:
        """``(autorizzata, URL a cui reindirizzare per posare il cookie)``.

        Il chiamante gestisce il redirect **prima** dell'inoltro: quando il
        secondo elemento non e' None la richiesta e' autorizzata ma non va
        inoltrata, va scambiata con un 302 che sposta la capability nel cookie.
        """
        if path == f"/{self._cap}" or path.startswith(f"/{self._cap}/"):
            stripped = path[len(self._cap) + 1:] or "/"
            return True, stripped if stripped.startswith("/") else "/" + stripped
        for name, value in header_lines:
            if name.lower() == "cookie" and _cookie_value(value, COOKIE_NAME) == self._cap:
                return True, None
        return False, None

    async def _forward_body_then_drain(
        self, reader: asyncio.StreamReader, up_writer: asyncio.StreamWriter, remaining: int
    ) -> None:
        """Inoltra i *remaining* byte di body ancora da leggere, poi **scarta** il resto.

        Quel che il client manda dopo il body e' un'altra richiesta, che non passa
        dalla riscrittura: non va inoltrata. Si continua pero' a leggere fino
        all'EOF, perche' e' la chiusura del client a dire che la risposta e'
        arrivata anche quando il server non chiude da se'.
        """
        try:
            while remaining > 0:
                chunk = await reader.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
                self._touch()
                up_writer.write(chunk)
                await up_writer.drain()
            while await reader.read(65536):
                pass
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            _close_quietly(up_writer)

    async def _pump_response(
        self, src: asyncio.StreamReader, dst: asyncio.StreamWriter
    ) -> None:
        """Come :meth:`_pump`, con ``Connection: close`` nella testa della risposta."""
        buf = bytearray()
        try:
            while _CRLF2 not in buf and len(buf) <= MAX_HEADER_BYTES:
                chunk = await src.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        if _CRLF2 in buf:
            head, _, rest = bytes(buf).partition(_CRLF2)
            dst.write(_close_response_head(head) + rest)
        else:
            dst.write(bytes(buf))
        self._touch()
        try:
            await dst.drain()
        except (ConnectionResetError, BrokenPipeError):
            return
        await self._pump(src, dst)

    async def _pump(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await src.read(65536)
                if not chunk:
                    break
                self._touch()
                dst.write(chunk)
                await dst.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                dst.write_eof()
            except Exception:
                pass


# ── helper di protocollo ─────────────────────────────────────────────────


def _close_quietly(writer: asyncio.StreamWriter) -> None:
    """Chiude uno writer senza propagare: la chiusura non e' mai la notizia."""
    try:
        writer.close()
    except Exception:
        pass


def _parse_head(head: bytes) -> tuple[tuple[str, str, str], list[tuple[str, str]]]:
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise ValueError("malformed request line")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise ValueError("malformed header line")
        headers.append((name.strip(), value.strip()))
    return (parts[0], parts[1], parts[2]), headers


# Header di connessione (hop-by-hop): il proxy decide lui come finisce la
# connessione, v. «Una richiesta per connessione» nel docstring del modulo.
_HOP_HEADERS = {"connection", "keep-alive", "proxy-connection"}


def _is_upgrade(headers: list[tuple[str, str]]) -> bool:
    """Richiesta di upgrade (WebSocket): ``Upgrade`` e ``Connection: upgrade``."""
    has_upgrade = any(name.lower() == "upgrade" for name, _ in headers)
    return has_upgrade and any(
        name.lower() == "connection" and "upgrade" in value.lower()
        for name, value in headers
    )


def _body_length(headers: list[tuple[str, str]]) -> int | None:
    """Byte di body dichiarati; ``None`` se ``chunked`` (non delimitabile qui)."""
    length = 0
    for name, value in headers:
        low = name.lower()
        if low == "transfer-encoding" and "chunked" in value.lower():
            return None
        if low == "content-length":
            try:
                length = max(0, int(value))
            except ValueError:
                return None
    return length


def _close_response_head(head: bytes) -> bytes:
    """La testa di una risposta con ``Connection: close`` al posto dei suoi header
    di connessione. Un 101 resta com'e': dopo non e' piu' HTTP."""
    lines = head.decode("latin-1").split("\r\n")
    if lines and " 101 " in f"{lines[0]} ":
        return head + _CRLF2
    kept = [lines[0]] + [
        line for line in lines[1:]
        if line and line.partition(":")[0].strip().lower() not in _HOP_HEADERS
    ]
    kept.append("Connection: close")
    return ("\r\n".join(kept) + "\r\n\r\n").encode("latin-1")


def _rebuild_head(
    method: str,
    path: str,
    version: str,
    headers: list[tuple[str, str]],
    *,
    host: str,
    upgrade: bool = False,
) -> bytes:
    out = [f"{method} {path} {version}"]
    for name, value in headers:
        low = name.lower()
        if low == "host" or low in _HOP_HEADERS:
            continue
        if low == "cookie":
            # La capability non deve raggiungere il server dell'utente: e' un
            # segreto nostro, e i cookie ignorano la porta (v. docstring).
            cleaned = _strip_cookie(value, COOKIE_NAME)
            if not cleaned:
                continue
            value = cleaned
        out.append(f"{name}: {value}")
    out.append(f"Host: {host}")
    out.append("Connection: Upgrade" if upgrade else "Connection: close")
    return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")


def _cookie_value(header: str, name: str) -> str | None:
    for item in header.split(";"):
        key, sep, value = item.strip().partition("=")
        if sep and key == name:
            return value
    return None


def _strip_cookie(header: str, name: str) -> str:
    kept = []
    for item in header.split(";"):
        key, sep, _ = item.strip().partition("=")
        if sep and key == name:
            continue
        if item.strip():
            kept.append(item.strip())
    return "; ".join(kept)


async def _respond(
    writer: asyncio.StreamWriter,
    status: int,
    reason: str,
    *,
    extra: list[tuple[str, str]] | None = None,
) -> None:
    lines = [f"HTTP/1.1 {status} {reason}"]
    for name, value in extra or []:
        lines.append(f"{name}: {value}")
    lines += ["Content-Length: 0", "Connection: close", "", ""]
    writer.write("\r\n".join(lines).encode("latin-1"))
    try:
        await writer.drain()
    except Exception:
        pass
