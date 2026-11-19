"""Il banco delle due Jenny nello stesso processo, per i test di ``jenny.peers``.

Ognuna ha il suo workspace, la sua ``config.json`` e un server WebSocket vero su
``127.0.0.1`` che passa ogni connessione a :meth:`PeerLink.serve`, come fa il
secondo ascoltatore del gateway sul percorso ``/peer``. Il controllo «solo
tailnet» è sostituito da un ``allow_address`` che ammette tutto: quel cancello
lo prova ``test_listener_routing.py``. La fixture ``two`` sta in ``conftest.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from websockets.asyncio.server import serve

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.peers import model
from jenny.peers.link import PeerLink


class Jenny:
    """Una Jenny di prova: workspace, config, link e il suo server."""

    def __init__(self, root: Path, name: str) -> None:
        self.root = root / name
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(parents=True)
        self.config_path = self.root / "config.json"
        cfg = Config()
        cfg.peers.enabled = True
        save_config(cfg, self.config_path)
        self.server = None
        self.link: PeerLink | None = None

    async def start(self, port: int = 0) -> None:
        async def handler(ws):
            assert self.link is not None
            # L'indirizzo da cui arriva la connessione: qui il loopback, l'unico
            # che risponde davvero quando l'altra Jenny poi lo richiama.
            await self.link.serve(ws, "127.0.0.1")

        self.server = await serve(handler, "127.0.0.1", port, max_size=model.MAX_FRAME_BYTES)
        bound = self.server.sockets[0].getsockname()[1]
        if self.link is None:
            # L'indirizzo con cui questa Jenny si presenta all'abbinamento: sul
            # telefono è il nome MagicDNS; qui il loopback, l'unico che risponde.
            async def self_address():
                return "127.0.0.1", None

            self.link = PeerLink(
                self.workspace,
                port=bound,
                allow_address=lambda _addr: True,
                self_address=self_address,
                config_path=self.config_path,
            )
            self.link.start()

    async def stop_server(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def close(self) -> None:
        # Prima la coda, poi il server: una coda ancora viva chiamerebbe l'altra
        # mentre il suo server si chiude. E con un tetto, così una chiusura che
        # non finisce è un errore con il suo nome e non una suite ferma.
        if self.link is not None:
            await asyncio.wait_for(self.link.stop(), timeout=15)
        await asyncio.wait_for(self.stop_server(), timeout=15)

    def peers(self):
        return load_config(self.config_path).peers.peers

    @property
    def id(self) -> str:
        assert self.link is not None
        return self.link.self_id()


async def pair(a: Jenny, b: Jenny) -> None:
    started = await a.link.start_pairing()
    await b.link.join(host="127.0.0.1", ip="", port=a.link.port, code=started["code"],
                      name="Personale")
