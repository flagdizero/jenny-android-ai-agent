"""Un socket grezzo da ``python_exec`` segue la stessa politica di ``http_get``.

``socket`` è bloccato solo come ``import socket``, ma ``asyncio`` è ammesso e
lo porta con sé. Misurato l'11/10/2026, prima della guardia, con il server di
questo file: ``asyncio.open_connection`` e ``asyncio.base_events.socket``
si connettevano entrambi a 127.0.0.1, cioè al telefono stesso, saltando
``validate_url_target``. Tutti i test passano da ``PythonExecTool.execute``,
il percorso di produzione (``run_in_executor`` su un worker): il percorso
sincrono non proverebbe niente.
"""

from __future__ import annotations

import socket
import threading

import pytest

from jenny.agent.tools.python_exec import PythonExecTool
from jenny.security.network import configure_ssrf_whitelist


@pytest.fixture
def listener():
    """Un server TCP su loopback che conta le connessioni ricevute.

    Rende ``(port, hits, received)``. ``hits`` lo riempie il thread di accept,
    non chi si connette: quando ``execute`` torna, il client ha già scritto e
    chiuso, ma il thread può non aver ancora fatto ``accept``/``recv`` (sotto
    ``-n auto`` su un runner carico è successo, ``assert [] == [b'hi']``).
    Chi aspetta una connessione chiama ``received(n)``, che attende l'``n``-esimo
    arrivo invece di leggere ``hits`` subito.
    """
    hits: list[bytes] = []
    arrived = threading.Condition()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    stop = threading.Event()

    def _accept() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(1)
                try:
                    data = conn.recv(64)
                except OSError:
                    data = b""
            with arrived:
                hits.append(data)
                arrived.notify_all()

    def received(n: int = 1, timeout: float = 5) -> list[bytes]:
        with arrived:
            arrived.wait_for(lambda: len(hits) >= n, timeout)
            return list(hits)

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()
    yield srv.getsockname()[1], hits, received
    stop.set()
    thread.join(timeout=2)
    srv.close()


@pytest.fixture
def no_whitelist():
    configure_ssrf_whitelist([])
    yield
    configure_ssrf_whitelist([])


def _tool(ws) -> PythonExecTool:
    return PythonExecTool(working_dir=str(ws), workspace=str(ws), restrict_to_workspace=True)


def _open_connection(port: int) -> str:
    return (
        "import asyncio\n"
        "async def main():\n"
        f"    r, w = await asyncio.open_connection('127.0.0.1', {port})\n"
        "    w.write(b'hi'); await w.drain(); w.close()\n"
        "asyncio.run(main())\n"
        "print('CONNECTED')\n"
    )


def _raw_socket(port: int) -> str:
    return (
        "import asyncio\n"
        "s = asyncio.base_events.socket.socket()\n"
        f"s.connect(('127.0.0.1', {port})); s.send(b'hi'); s.close()\n"
        "print('CONNECTED')\n"
    )


@pytest.mark.parametrize("make_code", [_open_connection, _raw_socket])
async def test_a_raw_socket_to_loopback_is_refused(tmp_path, listener, no_whitelist, make_code):
    port, hits, _ = listener
    out = await _tool(tmp_path).execute(code=make_code(port))
    assert "CONNECTED" not in out
    assert "private or internal address" in out
    assert hits == []


async def test_a_datagram_to_loopback_is_refused(tmp_path, listener, no_whitelist):
    port, _, _ = listener
    code = (
        "import asyncio\n"
        "s = asyncio.base_events.socket.socket(asyncio.base_events.socket.AF_INET,"
        " asyncio.base_events.socket.SOCK_DGRAM)\n"
        f"s.sendto(b'hi', ('127.0.0.1', {port}))\n"
        "print('SENT')\n"
    )
    out = await _tool(tmp_path).execute(code=code)
    assert "SENT" not in out
    assert "private or internal address" in out


async def test_a_unix_socket_is_refused(tmp_path, no_whitelist):
    code = (
        "import asyncio\n"
        "async def main():\n"
        "    await asyncio.open_unix_connection('/tmp/whatever.sock')\n"
        "asyncio.run(main())\n"
        "print('CONNECTED')\n"
    )
    out = await _tool(tmp_path).execute(code=code)
    assert "CONNECTED" not in out
    assert "only internet sockets" in out


async def test_an_allowed_destination_still_connects(tmp_path, listener, no_whitelist):
    """La guardia non toglie la rete: una destinazione permessa passa.

    Il loopback viene aperto con ``security.ssrfWhitelist``, che è anche
    l'eccezione che vale per ``http_get``: stessa politica, stessa porta.
    """
    port, _, received = listener
    configure_ssrf_whitelist(["127.0.0.0/8"])
    out = await _tool(tmp_path).execute(code=_open_connection(port))
    assert "CONNECTED" in out, out
    assert received(1) == [b"hi"]


def test_host_code_is_not_guarded(listener, no_whitelist):
    """Il gateway non vede la guardia: fuori da un exec è un passthrough."""
    from jenny.agent.tools.python_exec import _patch_socket_egress

    _patch_socket_egress()
    port, _, received = listener
    with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
        conn.sendall(b"host")
    assert received(1) == [b"host"]


async def test_the_c_level_socket_type_is_a_known_open_door(tmp_path, listener, no_whitelist):
    """Il tipo C ``_socket.socket`` non ha metodi rimpiazzabili: resta aperto.

    Come il thread grezzo di ``TestKnownRemainingDoors``, serve andarlo a cercare
    di proposito. Se un giorno fallisce, qualcuno ha chiuso anche questa porta:
    vanno aggiornati questo test e il commento su ``_patch_socket_egress``.
    """
    port, _, _ = listener
    code = (
        "import asyncio\n"
        "s = asyncio.base_events.socket._socket.socket()\n"
        f"s.connect(('127.0.0.1', {port})); s.send(b'c'); s.close()\n"
        "print('CONNECTED')\n"
    )
    out = await _tool(tmp_path).execute(code=code)
    assert "CONNECTED" in out, out
