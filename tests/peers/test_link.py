"""Due Jenny nello stesso processo: abbinamento, consegna, coda, scollegamento.

Ognuna ha il suo workspace, la sua ``config.json`` e un server WebSocket vero su
``127.0.0.1`` che passa ogni connessione a :meth:`PeerLink.serve`, come fa il
secondo ascoltatore del gateway sul percorso ``/peer``. Il controllo «solo
tailnet» è sostituito da un ``allow_address`` che ammette tutto: quel cancello
lo prova ``test_listener_routing.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.peers import model
from jenny.peers.link import PeerError, PeerLink
from jenny.peers.store import read_secret
from tests.support.aio import wait_until


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


@pytest.fixture()
async def two(tmp_path: Path):
    a, b = Jenny(tmp_path, "a"), Jenny(tmp_path, "b")
    await a.start()
    await b.start()
    yield a, b
    for j in (a, b):
        if j.link is not None:
            await asyncio.wait_for(j.link.stop(), timeout=15)
    await a.close()
    await b.close()


async def _pair(a: Jenny, b: Jenny) -> None:
    started = await a.link.start_pairing()
    await b.link.join(host="127.0.0.1", ip="", port=a.link.port, code=started["code"],
                      name="Personale")


async def test_pairing_gives_both_the_same_secret(two):
    a, b = two
    await _pair(a, b)

    assert [p.id for p in a.peers()] == [b.id]
    assert [p.id for p in b.peers()] == [a.id]
    assert b.peers()[0].name == "Personale"
    assert read_secret(a.workspace, b.id) == read_secret(b.workspace, a.id)
    assert read_secret(a.workspace, b.id) is not None
    # Chi ha mostrato il codice si è salvato l'indirizzo da cui l'altra è arrivata.
    assert a.peers()[0].ip == "127.0.0.1"
    # Il codice vale una volta sola.
    assert (await a.link.status())["pairing"] is None


async def test_a_secret_file_is_private_and_outside_the_workspace(two):
    a, b = two
    await _pair(a, b)
    path = a.root / "peers" / f"{b.id}.key"
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(a.workspace.rglob("*.key"))


async def test_a_wrong_code_is_refused_and_five_kill_it(two):
    a, b = two
    started = await a.link.start_pairing()
    wrong = "22222222" if started["raw_code"] != "22222222" else "33333333"
    for _ in range(5):
        with pytest.raises(PeerError) as err:
            await b.link.join(host="127.0.0.1", ip="", port=a.link.port, code=wrong,
                              name="Personale")
        assert err.value.code == "bad_code"
    with pytest.raises(PeerError) as err:
        await b.link.join(host="127.0.0.1", ip="", port=a.link.port, code=started["code"],
                          name="Personale")
    assert err.value.code == "no_code_open"
    assert a.peers() == [] and b.peers() == []


async def test_no_code_open_means_no_pairing(two):
    a, b = two
    with pytest.raises(PeerError) as err:
        await b.link.join(host="127.0.0.1", ip="", port=a.link.port, code="ABCD-EFGH",
                          name="Personale")
    assert err.value.code == "no_code_open"


async def test_a_malformed_code_never_dials(two):
    _a, b = two
    with pytest.raises(PeerError) as err:
        await b.link.join(host="127.0.0.1", ip="", port=1, code="nope", name="x")
    assert err.value.code == "bad_code"


async def test_a_ping_arrives_once_and_leaves_the_queue(two):
    a, b = two
    await _pair(a, b)
    await a.link.ping(b.id)
    await wait_until(lambda: any(r["kind"] == "ping" and r["direction"] == "in"
                                 for r in b.link.store.recent_log(a.id)), timeout=10)
    await wait_until(lambda: a.link.store.queued_count(b.id) == 0, timeout=10)


async def test_messages_wait_for_the_other_and_arrive_once(two, tmp_path):
    a, b = two
    await _pair(a, b)
    got: list[dict] = []

    async def on_note(peer_id, body):
        got.append(body)

    b.link.register("note", on_note)
    port = b.link.port
    await b.stop_server()

    await a.link.send(b.id, "note", {"n": 1})
    await wait_until(lambda: a.link.store.state(b.id)["last_error"] != "", timeout=10)
    assert a.link.store.queued_count(b.id) == 1
    assert got == []

    await b.start(port)
    a.link.wake(b.id)
    await wait_until(lambda: got == [{"n": 1}], timeout=10)
    await wait_until(lambda: a.link.store.queued_count(b.id) == 0, timeout=10)


async def test_a_wake_during_a_failing_attempt_is_not_lost(two):
    """Tailscale torna su (o l'utente tocca «Prova») mentre un tentativo fallisce:
    il rinvio scritto a fine tentativo non deve coprire quella sveglia."""
    a, b = two
    await _pair(a, b)
    await a.link.stop()  # la coda la gira il test, un giro alla volta

    async def deliver_and_fail(peer):
        a.link.wake(peer.id)  # arriva mentre il tentativo è in volo
        raise OSError("not yet")

    a.link._deliver = deliver_and_fail
    a.link.store.enqueue(b.id, "ping", {})
    await a.link._run_due()
    assert a.link.store.next_due_ms() == 0


async def test_stop_ends_the_queue_even_when_a_wake_races_the_cancel(two):
    """Su Python 3.11 ``wait_for`` può perdere una cancellazione che arriva insieme
    alla sveglia: la coda deve fermarsi lo stesso, subito."""
    a, _b = two
    for _ in range(20):
        a.link.start()
        await asyncio.sleep(0)
        a.link.kick()
        await asyncio.wait_for(a.link.stop(), timeout=5)


async def test_messages_leave_in_the_order_they_entered(tmp_path):
    from jenny.peers.store import PeerStore

    store = PeerStore(tmp_path / "workspace")
    ids = [store.enqueue("p" * 32, "note", {"n": n}, at_ms=1000) for n in range(10)]
    assert [row["id"] for row in store.pending("p" * 32)] == ids


async def test_a_resent_message_is_not_handled_twice(two):
    a, b = two
    await _pair(a, b)
    calls: list[dict] = []

    async def on_note(peer_id, body):
        calls.append(body)

    b.link.register("note", on_note)
    frame = {"type": "msg", "id": "a" * 32, "kind": "note", "body": {"x": 1}}
    assert (await b.link._receive(a.id, frame))["type"] == "ack"
    assert (await b.link._receive(a.id, frame))["type"] == "ack"
    assert calls == [{"x": 1}]


async def test_an_unknown_kind_is_refused_not_retried_forever(two):
    a, b = two
    await _pair(a, b)
    await a.link.send(b.id, "from_the_future", {})
    await wait_until(lambda: a.link.store.queued_count(b.id) == 0, timeout=10)
    refused = [r for r in a.link.store.recent_log(b.id) if r["kind"] == "refused"]
    assert refused and refused[0]["data"]["reason"] == "unsupported"


async def test_both_queues_flow_on_one_connection(two):
    a, b = two
    await _pair(a, b)
    seen_by_a: list[dict] = []
    seen_by_b: list[dict] = []

    async def note_a(peer_id, body):
        seen_by_a.append(body)

    async def note_b(peer_id, body):
        seen_by_b.append(body)

    a.link.register("note", note_a)
    b.link.register("note", note_b)
    # B ha un messaggio in coda ma non lo manda da sé: lo porta via la
    # connessione che apre A.
    b.link.store.enqueue(a.id, "note", {"from": "b"})
    await a.link.send(b.id, "note", {"from": "a"})
    await wait_until(lambda: seen_by_b == [{"from": "a"}] and seen_by_a == [{"from": "b"}],
                     timeout=10)


async def test_removing_says_goodbye_and_the_other_forgets_too(two):
    a, b = two
    await _pair(a, b)
    await a.link.remove(b.id)
    assert a.peers() == []
    assert read_secret(a.workspace, b.id) is None
    await wait_until(lambda: b.peers() == [], timeout=10)
    assert read_secret(b.workspace, a.id) is None


async def test_a_hello_from_a_stranger_is_closed(two):
    from websockets.asyncio.client import connect

    a, _b = two
    async with connect(f"ws://127.0.0.1:{a.link.port}{model.PEER_PATH}") as ws:
        await ws.send(json.dumps({
            "type": "hello", "v": 1, "from": "b" * 32, "to": a.id, "nonce": "c" * 32,
        }))
        with pytest.raises(Exception):
            await asyncio.wait_for(ws.recv(), timeout=5)


async def test_an_unexpected_first_frame_is_closed(two):
    from websockets.asyncio.client import connect

    a, _b = two
    async with connect(f"ws://127.0.0.1:{a.link.port}{model.PEER_PATH}") as ws:
        await ws.send(json.dumps({"type": "message", "content": "hi"}))
        with pytest.raises(Exception):
            await asyncio.wait_for(ws.recv(), timeout=5)


async def test_disabled_refuses_pairing(two):
    a, _b = two
    await a.link.set_enabled(False)
    with pytest.raises(PeerError) as err:
        await a.link.start_pairing()
    assert err.value.code == "disabled"


async def test_only_tailnet_addresses_are_dialled(tmp_path):
    j = Jenny(tmp_path, "c")
    link = PeerLink(j.workspace, port=18790, config_path=j.config_path)

    async def resolve(host, port):
        return ["192.168.1.20"]

    dialled: list[str] = []

    async def dial(uri):
        dialled.append(uri)
        raise OSError("should not be called")

    link._resolve = resolve
    link._dial = dial
    with pytest.raises(PeerError) as err:
        await link.join(host="nas.local", ip="", port=18790, code="ABCD-EFGH", name="x")
    assert err.value.code == "unreachable"
    assert dialled == []
