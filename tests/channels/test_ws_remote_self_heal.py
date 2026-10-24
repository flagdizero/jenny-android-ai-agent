"""L'ascoltatore Tailscale si ripara da solo.

Misurato sul Titan il 24/10/2026: Tailscale su, il Mac pingava il telefono, ma
la porta 18790 sul tailnet era chiusa. Ogni volta che Tailscale ristabilisce la
VPN, Android distrugge i socket legati agli indirizzi di ``tun0`` — anche quelli
in ascolto — e gli indirizzi tornano identici: il giro vedeva «stessi indirizzi,
server c'è» e non rifaceva niente, mentre asyncio girava a vuoto su ``accept()``.

Qui le regole che lo impediscono: un giro guarda se i socket sono vivi, non solo
gli indirizzi; il primo ``accept`` fallito sveglia il giro subito; il giro non
resta mai morto né bloccato. In fondo, la prova vera su Linux: un socket in
ascolto portato in ``TCP_CLOSE`` come fa ``SOCK_DESTROY`` di netd.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time
from typing import Any

import pytest
from port_alloc import free_port
from support.aio import wait_until
from websockets.asyncio.server import serve

from jenny.channels import ws_remote
from jenny.channels.ws_remote import RemoteListener, server_listening

TS4 = "100.94.12.7"
TS6 = "fd7a:115c:a1e0::1234"


class _FakeSock:
    def __init__(self) -> None:
        self.listening = True

    def fileno(self) -> int:
        return 42

    def getsockopt(self, level: int, option: int) -> int:
        assert (level, option) == (socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        return 1 if self.listening else 0


class _FakeServer:
    def __init__(self, hosts: tuple[str, ...], *, hang: bool = False) -> None:
        self.hosts = hosts
        self.sockets = [_FakeSock()]
        self.closed = False
        self._hang = hang

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        if self._hang:
            await asyncio.Event().wait()


class _Rig:
    def __init__(self, *, poll_s: float = 3600) -> None:
        self.addresses: tuple[str, ...] = (TS4, TS6)
        self.servers: list[_FakeServer] = []
        self.fail: OSError | None = None
        self.hang = False
        self.listener = RemoteListener(
            serve=self.serve,
            port=18790,
            detect=lambda: self.addresses,
            lookup_name=lambda ip: None,
            poll_s=poll_s,
        )

    async def serve(self, hosts: tuple[str, ...]) -> _FakeServer:
        if self.fail is not None:
            raise self.fail
        server = _FakeServer(hosts, hang=self.hang)
        self.servers.append(server)
        return server


def test_server_listening_reads_the_socket_state() -> None:
    server = _FakeServer((TS4,))
    assert server_listening(server)
    server.sockets[0].listening = False
    assert not server_listening(server)
    server.sockets = []
    assert not server_listening(server)  # chiuso: zero socket
    assert server_listening(object())  # non dice i suoi socket: si crede vivo


async def test_same_addresses_but_dead_sockets_means_rebind() -> None:
    rig = _Rig()
    await rig.listener.apply(enabled=True, key="k1")
    try:
        await rig.listener._sync()
        assert len(rig.servers) == 1  # vivo e stessi indirizzi: niente

        rig.servers[0].sockets[0].listening = False  # Android li ha distrutti
        await rig.listener._sync()
        assert rig.servers[0].closed
        assert [s.hosts for s in rig.servers] == [(TS4, TS6), (TS4, TS6)]
        status = await rig.listener.status()
        assert status["listening"]
        assert status["last_rebind"]["reason"] == "sockets destroyed"
    finally:
        await rig.listener.stop()


async def test_a_kick_runs_the_round_without_waiting_for_the_poll() -> None:
    rig = _Rig(poll_s=3600)
    await rig.listener.apply(enabled=True, key="k1")
    try:
        rig.servers[0].sockets[0].listening = False
        rig.listener.kick()
        await wait_until(lambda: len(rig.servers) == 2, timeout=2)
    finally:
        await rig.listener.stop()


async def test_a_failed_bind_with_tailscale_up_retries_soon(monkeypatch) -> None:
    monkeypatch.setattr(ws_remote, "_RETRY_S", 0.05)
    rig = _Rig(poll_s=3600)
    rig.fail = OSError(99, "Cannot assign requested address")
    await rig.listener.apply(enabled=True, key="k1")
    try:
        assert rig.servers == []
        rig.fail = None
        await wait_until(lambda: len(rig.servers) == 1, timeout=2)
    finally:
        await rig.listener.stop()


async def test_a_watch_that_dies_is_restarted() -> None:
    rig = _Rig(poll_s=3600)
    await rig.listener.apply(enabled=True, key="k1")
    try:
        first = rig.listener._watch
        assert first is not None

        real = rig.listener._kick

        class _Boom:
            def __init__(self) -> None:
                self.armed = True

            async def wait(self) -> None:
                if self.armed:
                    self.armed = False
                    raise RuntimeError("boom")
                await real.wait()

            def set(self) -> None:
                real.set()

            def clear(self) -> None:
                real.clear()

        rig.listener._kick = _Boom()  # type: ignore[assignment]
        first.cancel()  # il giro in corso aspetta ancora il vero evento
        await asyncio.gather(first, return_exceptions=True)
        rig.listener._start_watch()  # questo morirà al primo giro
        dying = rig.listener._watch
        await wait_until(lambda: rig.listener._watch is not dying, timeout=2)

        rig.servers[0].sockets[0].listening = False
        rig.listener.kick()
        await wait_until(lambda: len(rig.servers) == 2, timeout=2)
    finally:
        await rig.listener.stop()


async def test_apply_replaces_a_watch_that_has_finished() -> None:
    rig = _Rig()
    await rig.listener.apply(enabled=True, key="k1")
    try:
        old = rig.listener._watch
        assert old is not None
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        await rig.listener.apply(enabled=True, key="k1")
        assert rig.listener._watch is not old and not rig.listener._watch.done()
    finally:
        await rig.listener.stop()


async def test_a_server_that_never_closes_does_not_hold_the_lock(monkeypatch) -> None:
    monkeypatch.setattr(ws_remote, "_CLOSE_TIMEOUT_S", 0.05)
    rig = _Rig()
    rig.hang = True
    await rig.listener.apply(enabled=True, key="k1")
    await asyncio.wait_for(rig.listener.stop(), timeout=2)
    assert rig.servers[0].closed and not rig.listener._lock.locked()


async def test_a_slow_detection_skips_the_round_and_keeps_a_healthy_server(monkeypatch) -> None:
    monkeypatch.setattr(ws_remote, "_DETECT_TIMEOUT_S", 0.05)
    rig = _Rig()
    await rig.listener.apply(enabled=True, key="k1")
    try:

        def slow() -> tuple[str, ...]:
            time.sleep(0.3)
            return ()

        rig.listener._detect = slow
        await rig.listener._sync()
        assert len(rig.servers) == 1 and not rig.servers[0].closed
    finally:
        await rig.listener.stop()


async def test_the_guard_passes_other_errors_on_and_is_removed_on_stop() -> None:
    loop = asyncio.get_running_loop()
    seen: list[dict[str, Any]] = []

    def previous(_loop: Any, context: dict[str, Any]) -> None:
        seen.append(context)

    loop.set_exception_handler(previous)
    try:
        rig = _Rig()
        await rig.listener.apply(enabled=True, key="k1")
        assert loop.get_exception_handler() is not previous
        loop.call_exception_handler({"message": "altro", "exception": ValueError("x")})
        assert [c["message"] for c in seen] == ["altro"]
        await rig.listener.stop()
        assert loop.get_exception_handler() is previous
    finally:
        loop.set_exception_handler(None)


# -- La prova vera: un socket in ascolto distrutto sotto i piedi ---------------


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="shutdown() porta un socket in ascolto in TCP_CLOSE solo su Linux",
)
async def test_a_destroyed_listening_socket_is_rebound_at_once() -> None:
    """Come ``SOCK_DESTROY`` di netd: il socket resta aperto e registrato, ma
    non è più in ``LISTEN``. Prima del fix: ``accept`` → ``EINVAL`` a ogni giro del
    loop, per sempre. Il giro è a un'ora: se si ripara, è la sveglia."""
    loop = asyncio.get_running_loop()
    reported: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    port = free_port()

    async def handler(connection: Any) -> None:
        await connection.wait_closed()

    async def serve_real(hosts: tuple[str, ...]) -> Any:
        return await serve(handler, list(hosts), port)  # type: ignore[arg-type]

    listener = RemoteListener(
        serve=serve_real,
        port=port,
        detect=lambda: ("127.0.0.1",),
        lookup_name=lambda ip: None,
        poll_s=3600,
    )
    try:
        await listener.apply(enabled=True, key="k1")
        first = listener._server
        assert first is not None and server_listening(first)

        dup = socket.fromfd(first.sockets[0].fileno(), socket.AF_INET, socket.SOCK_STREAM)
        dup.shutdown(socket.SHUT_RDWR)
        dup.close()
        assert not server_listening(first)

        await wait_until(
            lambda: listener._server is not None and listener._server is not first,
            timeout=2,
            msg="l'ascoltatore distrutto non è stato rifatto",
        )
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        assert (await listener.status())["last_rebind"]["reason"] == "sockets destroyed"
        # Gli ``accept`` falliti erano nostri: non arrivano al gestore di prima.
        assert reported == []
    finally:
        await listener.stop()
        loop.set_exception_handler(None)
