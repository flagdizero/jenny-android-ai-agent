"""Il proxy su loopback per la vista esterna di una Jenny App.

Esiste perche' la policy di rete dell'APK rifiuta un iframe verso un ``http://``
non-loopback (``ERR_CLEARTEXT_NOT_PERMITTED``), e perche' il gateway non puo'
fare da reverse proxy: gira sul parser di ``websockets``, che non legge il body.

I test si dividono in due famiglie, e la divisione e' deliberata:

- il **cancello** (SSRF, scheme) provato con il validatore vero;
- il **trasporto** provato con il validatore sostituito, perche' un upstream di
  prova sta su 127.0.0.1 e il validatore vero — giustamente — lo blocca.

Il primo gruppo contiene un test che verifica che il cancello venga *chiamato*,
cosi' sostituirlo negli altri non puo' nascondere una sua rimozione.
"""

from __future__ import annotations

import asyncio

import pytest
from support.aio import wait_until

from jenny.apps import proxy as proxy_mod
from jenny.apps.proxy import COOKIE_NAME, AppViewProxy, AppViewProxyError

# ── il cancello ──────────────────────────────────────────────────────────


async def test_loopback_upstream_is_refused():
    """Il gateway vive su loopback: un'app non deve poterlo incorniciare."""
    p = AppViewProxy("x", "http://127.0.0.1:9")
    with pytest.raises(AppViewProxyError, match="blocked server target"):
        await p.start()


async def test_https_upstream_is_refused_with_a_useful_reason():
    """Un server https non ha bisogno di questo proxy, e il messaggio lo dice."""
    with pytest.raises(AppViewProxyError, match="do not need the view proxy"):
        AppViewProxy("x", "https://example.com")


async def test_start_actually_consults_the_ssrf_gate(monkeypatch):
    """Il cancello deve essere interrogato, non solo esistere.

    Gli altri test lo sostituiscono per poter usare un upstream su 127.0.0.1:
    senza questo, togliere la chiamata da ``start()`` li lascerebbe tutti verdi.
    """
    calls = []

    def _fake(url):
        calls.append(url)
        return False, "nope"

    monkeypatch.setattr(proxy_mod, "validate_app_server_target", _fake)
    p = AppViewProxy("x", "http://plant.lan:8080")
    with pytest.raises(AppViewProxyError):
        await p.start()
    assert calls == ["http://plant.lan:8080"]


# ── il trasporto ─────────────────────────────────────────────────────────


class _Upstream:
    """Server di prova: risponde riportando cosa ha ricevuto."""

    def __init__(self):
        self.server = None
        self.port = None
        self.seen: list[bytes] = []

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def _handle(self, reader, writer):
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buf.extend(chunk)
        head, _, rest = bytes(buf).partition(b"\r\n\r\n")
        # Legge il body dichiarato, se c'e'.
        length = 0
        for line in head.decode("latin-1").split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip() or 0)
        body = bytearray(rest)
        while len(body) < length:
            chunk = await reader.read(length - len(body))
            if not chunk:
                break
            body.extend(chunk)
        self.seen.append(bytes(head) + b"\r\n\r\n" + bytes(body))
        payload = b"UPSTREAM-OK"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (len(payload), payload)
        )
        await writer.drain()
        writer.close()

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()


@pytest.fixture
async def wired(monkeypatch):
    """(proxy avviato, upstream) con il cancello SSRF sostituito."""
    monkeypatch.setattr(proxy_mod, "validate_app_server_target", lambda url: (True, ""))
    up = _Upstream()
    port = await up.start()
    p = AppViewProxy("telecomando", f"http://127.0.0.1:{port}")
    await p.start()
    try:
        yield p, up
    finally:
        await p.close()
        await up.close()


async def _raw(port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    out = await asyncio.wait_for(reader.read(-1), timeout=5)
    writer.close()
    return out


async def test_binds_only_on_loopback(wired):
    p, _ = wired
    host, port = p._server.sockets[0].getsockname()[:2]
    assert host == "127.0.0.1", "il listener non deve stare su un'interfaccia esterna"
    assert port == p.port


async def test_request_without_capability_is_forbidden(wired):
    p, up = wired
    out = await _raw(p.port, b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    assert b"403" in out.split(b"\r\n")[0]
    assert up.seen == [], "una richiesta non autorizzata non deve raggiungere il server"


async def test_capability_path_redirects_and_sets_the_cookie(wired):
    """La capability passa dal path a un cookie.

    Serve perche' un prefisso di path romperebbe i path assoluti della pagina
    remota (`/style.css` si risolverebbe fuori dal prefisso). Col cookie
    l'origine e' la radice e i path assoluti funzionano.
    """
    p, up = wired
    out = await _raw(p.port, f"GET /{p._cap}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    head = out.decode("latin-1")
    assert "302" in head.split("\r\n")[0]
    assert f"{COOKIE_NAME}={p._cap}" in head
    assert "Path=/" in head and "HttpOnly" in head
    assert "Location: /" in head
    assert up.seen == [], "il redirect non deve toccare il server"


async def test_cookie_authorizes_and_the_request_is_forwarded(wired):
    p, up = wired
    out = await _raw(
        p.port,
        f"GET /style.css HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        f"Cookie: {COOKIE_NAME}={p._cap}\r\n\r\n".encode(),
    )
    assert b"UPSTREAM-OK" in out
    assert len(up.seen) == 1
    got = up.seen[0].decode("latin-1")
    assert got.startswith("GET /style.css HTTP/1.1")


async def test_capability_is_stripped_before_reaching_the_users_server(wired):
    """Il segreto e' nostro: il server dell'utente non deve vederlo.

    I cookie sono per-host e ignorano la porta, quindi il browser lo manda a
    qualunque cosa su 127.0.0.1; qui e' dove la catena si interrompe.
    """
    p, up = wired
    await _raw(
        p.port,
        f"GET /x HTTP/1.1\r\nHost: h\r\n"
        f"Cookie: {COOKIE_NAME}={p._cap}; sid=abc\r\n\r\n".encode(),
    )
    got = up.seen[0].decode("latin-1")
    assert p._cap not in got
    assert COOKIE_NAME not in got
    assert "sid=abc" in got, "gli altri cookie della pagina devono passare"


async def test_post_with_a_body_passes_through(wired):
    """La ragione per cui il proxy non e' una route del gateway.

    Il parser di ``websockets`` non legge il body: un POST non sarebbe potuto
    passare, e una UI di telecomando reale ne fa.
    """
    p, up = wired
    body = b'{"cmd":"volume_up"}'
    await _raw(
        p.port,
        b"POST /api/cmd HTTP/1.1\r\nHost: h\r\n"
        b"Cookie: " + COOKIE_NAME.encode() + b"=" + p._cap.encode() + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: %d\r\n\r\n%s" % (len(body), body),
    )
    got = up.seen[0]
    assert got.startswith(b"POST /api/cmd HTTP/1.1")
    assert body in got


async def test_host_header_is_rewritten_to_the_upstream(wired):
    p, up = wired
    await _raw(
        p.port,
        f"GET /x HTTP/1.1\r\nHost: 127.0.0.1:{p.port}\r\n"
        f"Cookie: {COOKIE_NAME}={p._cap}\r\n\r\n".encode(),
    )
    got = up.seen[0].decode("latin-1")
    assert f"Host: 127.0.0.1:{up.port}" in got
    assert f"Host: 127.0.0.1:{p.port}" not in got


async def test_two_proxies_get_different_capabilities(monkeypatch):
    """Il segreto e' per-listener: uno non apre la vista di un'altra app."""
    monkeypatch.setattr(proxy_mod, "validate_app_server_target", lambda url: (True, ""))
    a = AppViewProxy("a", "http://127.0.0.1:9001")
    b = AppViewProxy("b", "http://127.0.0.1:9002")
    assert a._cap != b._cap
    assert len(a._cap) >= 20


async def test_close_releases_the_port(wired):
    p, _ = wired
    port = p.port
    await p.close()
    with pytest.raises((ConnectionRefusedError, OSError)):
        await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=5)


async def test_malformed_request_line_gets_400_not_a_crash(wired):
    p, up = wired
    out = await _raw(p.port, b"GARBAGE\r\nHost: x\r\n\r\n")
    assert b"400" in out.split(b"\r\n")[0]
    assert up.seen == []


async def test_idle_timeout_actually_closes_the_listener(monkeypatch):
    """L'idle timeout deve chiudere davvero, non a metà — **con una connessione
    aperta**, che è la condizione in cui il difetto si manifesta.

    Cosa fissa: l'ordine dei passi in ``close()``. ``wait_closed()`` attende
    anche i gestori delle connessioni vive (da 3.12), e un gestore del proxy sta
    normalmente appeso su ``read()``: aspettarlo prima di cancellarlo è un
    deadlock. Verificato invertendo l'ordine — questo test fallisce.

    Senza la connessione tenuta aperta qui sotto il test passava comunque, quindi
    quella riga è il test. Su 3.11 (la versione del telefono, Chaquopy)
    ``wait_closed()`` non sospende e il deadlock non si presenta; su 3.14 e sulla
    3.12 della CI sì.
    """
    monkeypatch.setattr(proxy_mod, "validate_app_server_target", lambda url: (True, ""))
    up = _Upstream()
    await up.start()
    p = AppViewProxy("effimera", f"http://127.0.0.1:{up.port}", idle_timeout_s=0.2)
    await p.start()
    port = p.port
    # Connessione aperta e muta: il gestore resta appeso su read(), quindi
    # `wait_closed()` ha davvero qualcosa da aspettare.
    _, idle_writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await wait_until(
            lambda: p._server is None,
            timeout=5.0,
            interval=0.05,
            msg="il reaper non ha portato a termine la chiusura",
        )
        assert p._conns == set(), "le connessioni non sono state cancellate"
        with pytest.raises((ConnectionRefusedError, OSError)):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=5)
    finally:
        idle_writer.close()
        await p.close()
        await up.close()


async def test_traffic_keeps_the_listener_alive(monkeypatch):
    """Il timeout è sull'inattività: chi la sta usando non deve perderla."""
    monkeypatch.setattr(proxy_mod, "validate_app_server_target", lambda url: (True, ""))
    up = _Upstream()
    await up.start()
    p = AppViewProxy("viva", f"http://127.0.0.1:{up.port}", idle_timeout_s=0.6)
    await p.start()
    try:
        for _ in range(4):
            await asyncio.sleep(0.2)
            await _raw(
                p.port,
                f"GET /ping HTTP/1.1\r\nHost: h\r\n"
                f"Cookie: {COOKIE_NAME}={p._cap}\r\n\r\n".encode(),
            )
        assert p._server is not None, "il traffico deve rinviare la chiusura"
    finally:
        await p.close()
        await up.close()


async def test_close_returns_promptly_with_a_live_connection(monkeypatch):
    """Il percorso che usa l'utente: chiudere l'app con la pagina caricata.

    La UI chiama `.../view/close` mentre una connessione è ancora aperta — è la
    norma, non un caso limite. Con `wait_closed()` atteso prima di cancellare i
    gestori quella chiamata non ritornava mai, e la route restava appesa.
    Qui il timeout è l'asserzione.
    """
    monkeypatch.setattr(proxy_mod, "validate_app_server_target", lambda url: (True, ""))
    up = _Upstream()
    await up.start()
    p = AppViewProxy("aperta", f"http://127.0.0.1:{up.port}")
    await p.start()
    _, writer = await asyncio.open_connection("127.0.0.1", p.port)
    await asyncio.sleep(0.05)
    assert p._conns, "il gestore della connessione deve essere vivo"
    try:
        await asyncio.wait_for(p.close(), timeout=5)
    except asyncio.TimeoutError:  # pragma: no cover - è il difetto
        pytest.fail("close() non ritorna con una connessione aperta (deadlock)")
    finally:
        writer.close()
        await up.close()
    assert p._server is None
