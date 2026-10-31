"""Allocatore di porte condiviso per i test di ``tests/channels/``.

I test di questa cartella aprono server veri su ``127.0.0.1``. Con porte fisse
due sessioni di pytest sullo stesso host si contendono la stessa porta e la
seconda muore con ``OSError: [Errno 48] Address already in use``: una
collisione deterministica, non un problema di tempi. Ogni test deve quindi
chiedere qui la sua porta::

    from port_alloc import free_port

    port = free_port()

L'import "nudo" funziona perché pytest mette la cartella del test in
``sys.path`` (nessun ``__init__.py``), lo stesso meccanismo con cui i test
importano ``ws_test_client``.
"""

from __future__ import annotations

import asyncio
import random
import socket
from typing import Any

_LOW = 30_000
_HIGH = 60_000
_ATTEMPTS = 100


def free_port() -> int:
    """Restituisce una porta TCP libera su localhost.

    Sonda porte casuali fuori dall'intervallo effimero tipico finché una
    ``bind`` riesce, poi chiude il socket e restituisce il numero. Fra la
    chiusura e la ``bind`` del canale resta una finestra TOCTOU: a questo
    intervallo è trascurabile ed è il compromesso già in uso nel repo.
    """
    for _ in range(_ATTEMPTS):
        port = random.randint(_LOW, _HIGH)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("could not find a free localhost port")


async def wait_listening(channel: Any, *, timeout: float = 5.0) -> None:
    """Aspetta che il canale avviato con ``create_task(channel.start())`` accetti
    connessioni sulla sua porta.

    Prima ogni test aspettava 0,3 s fissi: troppo per un server che è su in
    pochi millisecondi, e poco su una macchina carica. Qui si prova a connettersi
    ogni 10 ms finché la porta risponde, fino a *timeout*.
    """
    host, port = channel.config.host, channel.config.port
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection(host, port)
        except OSError:
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f"{host}:{port} non ascolta dopo {timeout}s") from None
            await asyncio.sleep(0.01)
            continue
        writer.close()
        await writer.wait_closed()
        return
