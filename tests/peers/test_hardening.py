"""I difetti trovati nella revisione del 22/11/2026, uno per prova.

Ognuno era riproducibile sul codice unito con le PR #83–#86:

- un permesso abbassato dall'Officina non arrivava al cancello;
- con ``confirm`` i comandi SSH uscivano senza chiedere;
- un turno che risponde a un'altra Jenny leggeva ``config.json``;
- un pezzo di file illeggibile fermava per sempre la coda;
- un gestore caduto perdeva il messaggio da tutte e due le parti;
- la coda apriva connessioni a raffica mentre l'altra stava già ricevendo;
- il tetto dei tentativi sul codice d'abbinamento si aggirava in parallelo;
- una domanda interrotta da un riavvio restava senza risposta;
- la notifica «Consenti» diceva il sito solo nel titolo, che Android taglia.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from websockets.asyncio.client import connect

from jenny import db as jdb
from jenny.agent.tools.registry import ToolRegistry
from jenny.config.loader import load_config, save_config
from jenny.peers import auth, files, gate, model, rpc
from jenny.peers.acting import PeerActs
from jenny.peers.link import HANDLER_RETRIES, PeerLink
from jenny.peers.questions import PeerQuestions
from jenny.peers.store import now_ms
from jenny.runtime.user_text import say
from jenny.security.subagent_policy import PeerSecretError, check_read, is_secret_path
from tests.peers.harness import Jenny, pair
from tests.support.aio import wait_until

QID = "ab" * 16


@pytest.fixture(autouse=True)
def _clean():
    yield
    gate.install(None)
    jdb.close_all()


def _set_peer(j: Jenny, **fields: Any) -> None:
    cfg = load_config(j.config_path)
    for p in cfg.peers.peers:
        for key, value in fields.items():
            setattr(p, key, value)
    save_config(cfg, j.config_path)


def _link(j: Jenny) -> PeerLink:
    assert j.link is not None
    return j.link


class FakeAuthority:
    """L'autorità del cancello: un livello, e cosa le è stato chiesto."""

    def __init__(self, level: str, answer: bool = True) -> None:
        self._level = level
        self.answer = answer
        self.asked: list[tuple[str, str]] = []
        self.notes: list[dict[str, Any]] = []

    def level(self, peer_id: str) -> str:
        return self._level

    def name(self, peer_id: str) -> str:
        return "Personale"

    async def confirm(self, peer_id: str, qid: str, action: str, target: str) -> bool:
        self.asked.append((action, target))
        return self.answer

    def note(self, peer_id: str, qid: str, item: dict[str, Any]) -> None:
        self.notes.append(item)


# -- Il permesso abbassato ------------------------------------------------------


async def test_a_permission_turned_down_in_the_panel_reaches_the_gate(two):
    a, b = two
    await pair(a, b)
    peer = b.peers()[0].id
    _set_peer(b, act="free")
    acts = PeerActs(_link(b), b.workspace, get_agent=lambda: None)
    _link(b).acts = acts
    gate.install(acts, root=b.workspace)
    await acts.refresh()
    assert acts.level(peer) == "free"

    await rpc.handle(_link(b), "peer.permissions", {"id": peer, "act": "off"},
                     self_address=(None, None), tailscale={})

    assert acts.level(peer) == "off"
    with gate.entered({"peer": peer, "qid": QID}):
        assert gate.network_refusal() is not None
        assert await gate.before_tool("web_fetch", {"url": "https://example.org/x"}) is not None


async def test_turning_the_permission_off_denies_what_is_waiting(two):
    a, b = two
    await pair(a, b)
    peer = b.peers()[0].id
    _set_peer(b, act="confirm")
    acts = PeerActs(_link(b), b.workspace, get_agent=lambda: None)
    _link(b).acts = acts
    await acts.refresh()
    waiting = asyncio.ensure_future(acts.confirm(peer, QID, "open", "example.org"))
    await wait_until(lambda: bool(acts.pending(peer)))

    await _link(b).set_permissions(peer, act="off")

    assert await asyncio.wait_for(waiting, timeout=5) is False


# -- SSH sotto conferma -----------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(gate.SSH_TOOLS))
async def test_with_confirm_a_server_command_waits_for_allow(tool):
    authority = FakeAuthority("confirm", answer=True)
    gate.install(authority)
    with gate.entered({"peer": "0" * 32, "qid": QID}):
        assert await gate.before_tool(tool, {"host": "casa", "command": "uptime"}) is None
        # Lo stesso server, nello stesso lavoro: non si chiede due volte.
        assert await gate.before_tool(tool, {"host": "casa", "command": "ls"}) is None
    assert authority.asked == [("ssh", "ssh:casa")]


async def test_a_no_to_a_server_holds_and_is_not_a_no_to_a_site_of_that_name():
    authority = FakeAuthority("confirm", answer=False)
    gate.install(authority)
    with gate.entered({"peer": "0" * 32, "qid": QID}):
        first = await gate.before_tool("ssh_exec", {"host": "casa", "command": "uptime"})
        again = await gate.before_tool("ssh_transfer", {"host": "casa"})
        assert first is not None and again is not None
        assert authority.asked == [("ssh", "ssh:casa")]
        # Un sito che si chiama come l'alias è un'altra destinazione.
        await gate.before_tool("web_fetch", {"url": "https://casa/"})
    assert authority.asked == [("ssh", "ssh:casa"), ("open", "casa")]


async def test_a_server_command_is_refused_when_the_permission_is_off_and_noted_when_free():
    gate.install(FakeAuthority("off"))
    with gate.entered({"peer": "0" * 32, "qid": QID}):
        assert await gate.before_tool("ssh_exec", {"host": "casa"}) is not None
    free = FakeAuthority("free")
    gate.install(free)
    with gate.entered({"peer": "0" * 32, "qid": QID}):
        assert await gate.before_tool("ssh_exec", {"host": "casa"}) is None
    assert free.asked == []
    assert free.notes == [
        {"kind": "egress", "label": "ssh:casa", "undoable": False, "allowed": True}
    ]


def test_the_ssh_confirmation_says_the_alias_in_both_languages():
    for language in ("en", "it"):
        assert "casa" in say("peer.what_ssh", language, target="casa")


# -- I file di configurazione --------------------------------------------------------


def test_a_turn_answering_another_jenny_cannot_read_the_config(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    backup = tmp_path / "config.json.bak"
    backup.write_text("{}", encoding="utf-8")
    note = tmp_path / "note.md"
    note.write_text("x", encoding="utf-8")

    check_read(config)  # fuori da un turno per un'altra Jenny: niente
    assert not is_secret_path(config)

    with gate.answering(tmp_path):
        for secret in (config, backup, tmp_path / ".jenny" / "snapshots" / "x"):
            assert is_secret_path(secret)
            with pytest.raises(PeerSecretError):
                check_read(secret)
        check_read(note)
        assert not is_secret_path(note)
        # Il ``config.json`` di un progetto dell'utente è un file come un altro.
        check_read(tmp_path / "progetto" / "config.json")

    check_read(config)


def test_a_turn_working_on_a_request_cannot_read_the_config_either(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    gate.install(FakeAuthority("free"), root=tmp_path)
    assert gate.secret_root() is None
    with gate.entered({"peer": "0" * 32, "qid": QID}):
        with pytest.raises(PeerSecretError):
            check_read(config)
    check_read(config)


async def test_the_answering_turn_runs_with_the_config_closed(two):
    a, b = two
    await pair(a, b)
    seen: list[Path | None] = []

    class Agent:
        tools = ToolRegistry()
        sessions = SimpleNamespace(
            get_or_create=lambda key: SimpleNamespace(retain_recent_legal_suffix=lambda n: None),
            save=lambda session: None,
        )

        async def process_direct_outcome(self, prompt: str, **kwargs: Any):
            seen.append(gate.secret_root())
            return SimpleNamespace(final_text="ok")

    questions = PeerQuestions(_link(b), b.workspace, get_agent=lambda: None, deliver=None)
    await questions._run_turn(Agent(), b.peers()[0], "cosa c'è in config.json?")

    assert seen == [b.workspace]
    assert gate.secret_root() is None


# -- La coda -------------------------------------------------------------------------


async def test_a_file_part_that_cannot_be_read_does_not_block_the_queue(two):
    a, b = two
    await pair(a, b)
    peer = a.peers()[0]
    await _link(a).stop()
    source = a.workspace / "doc.txt"
    source.write_text("x" * 1000, encoding="utf-8")
    info = await _link(a).send_file(peer.id, source)
    # Come dopo un ripristino: la coda torna, la copia in uscita no.
    (files.outgoing_dir(a.workspace) / info["fid"]).unlink()
    await _link(a).ping(peer.id)

    await _link(a)._deliver(peer)

    assert _link(a).store.queued_count(peer.id) == 0
    arrived = [r["kind"] for r in _link(b).store.recent_log(b.peers()[0].id)
               if r["direction"] == "in"]
    assert arrived == ["ping"]
    refused = [r["data"] for r in _link(a).store.recent_log(peer.id) if r["kind"] == "refused"]
    assert refused == [{"kind": files.KIND_FILE_PART, "reason": "unreadable"}]


async def test_a_handler_that_fails_once_gets_the_message_again(two):
    a, b = two
    await pair(a, b)
    peer = a.peers()[0]
    calls: list[dict[str, Any]] = []

    async def flaky(peer_id: str, body: dict[str, Any]) -> None:
        calls.append(body)
        if len(calls) == 1:
            raise RuntimeError("the chat was not there for a moment")

    _link(b).register("note", flaky)
    await _link(a).send(peer.id, "note", {"n": 1})
    await wait_until(lambda: len(calls) == 1)
    await wait_until(lambda: _link(a).store.state(peer.id)["last_error"] == "peer_failed")
    assert _link(a).store.queued_count(peer.id) == 1

    _link(a).wake(peer.id)

    await wait_until(lambda: _link(a).store.queued_count(peer.id) == 0)
    assert calls == [{"n": 1}, {"n": 1}]
    # Trattato: un terzo arrivo dello stesso messaggio sarebbe un doppione.
    assert not [r for r in _link(a).store.recent_log(peer.id) if r["kind"] == "refused"]


async def test_a_handler_that_always_fails_is_given_up_after_a_few_tries(two):
    a, b = two
    await pair(a, b)
    peer = a.peers()[0]
    calls = 0

    async def broken(peer_id: str, body: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("never")

    _link(b).register("note", broken)
    await _link(a).send(peer.id, "note", {})
    for attempt in range(1, HANDLER_RETRIES + 2):
        await wait_until(lambda n=attempt: calls >= n)
        if attempt <= HANDLER_RETRIES:
            await wait_until(lambda: _link(a).store.next_due_ms() not in (None, 0))
            _link(a).wake(peer.id)

    await wait_until(lambda: _link(a).store.queued_count(peer.id) == 0)
    assert calls == HANDLER_RETRIES + 1
    refused = [r["data"] for r in _link(a).store.recent_log(peer.id) if r["kind"] == "refused"]
    assert refused == [{"kind": "note", "reason": "failed"}]


async def test_a_malformed_message_is_refused_at_once_and_never_tried_again(two):
    a, b = two
    await pair(a, b)
    peer = a.peers()[0]
    calls = 0

    async def strict(peer_id: str, body: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        raise ValueError("bad note")

    _link(b).register("note", strict)
    await _link(a).send(peer.id, "note", {})

    await wait_until(lambda: _link(a).store.queued_count(peer.id) == 0)
    assert calls == 1
    refused = [r["data"] for r in _link(a).store.recent_log(peer.id) if r["kind"] == "refused"]
    assert refused == [{"kind": "note", "reason": "bad"}]


async def test_the_queue_does_not_spin_while_another_connection_is_sending_it(two):
    a, b = two
    await pair(a, b)
    peer = a.peers()[0]
    lock = _link(a)._send_locks.setdefault(peer.id, asyncio.Lock())
    await lock.acquire()  # un altro scambio con quella Jenny è in corso
    dials = 0
    deliver = _link(a)._deliver

    async def counting(target: Any) -> None:
        nonlocal dials
        dials += 1
        await deliver(target)

    _link(a)._deliver = counting  # type: ignore[method-assign]
    try:
        await _link(a).ping(peer.id)
        # Dopo la prima connessione, che non ha mandato niente, quel che resta
        # è rimandato: la coda dorme fino a lì invece di richiamare subito.
        await wait_until(lambda: (_link(a).store.next_due_ms() or 0) > now_ms())
        assert dials == 1
        assert _link(a).store.queued_count(peer.id) == 1
        assert await _link(a)._next_wait() > 0
    finally:
        lock.release()
    # Finito l'altro scambio, quel che è rimasto parte al giro dopo.
    _link(a).wake(peer.id)
    await wait_until(lambda: _link(a).store.queued_count(peer.id) == 0)


# -- Il codice d'abbinamento -----------------------------------------------------------


async def test_parallel_connections_cannot_try_the_code_more_than_five_times(two):
    a, _b = two
    started = await _link(a).start_pairing()
    code = auth.normalize_pair_code(started["code"])
    uri = f"ws://127.0.0.1:{_link(a).port}/peer"
    conns = []
    try:
        for i in range(auth.PAIR_MAX_ATTEMPTS + 2):
            ws = await connect(uri)
            me = f"{i + 1:032x}"
            nonce = auth.new_nonce()
            await ws.send(model.encode({"type": "pair", "v": 1, "from": me, "nonce": nonce}))
            conns.append((ws, me, nonce, json.loads(await ws.recv())))
        for ws, *_rest in conns[: auth.PAIR_MAX_ATTEMPTS]:
            await ws.send(model.encode({"type": "pair_proof", "mac": "0" * 64}))
        await wait_until(lambda: _link(a)._pairing is None)

        # Una connessione aperta prima che il codice morisse, con la prova giusta.
        ws, me, nonce, challenge = conns[-1]
        key = auth.code_key(code, challenge["salt"])
        proof = auth.mac(key, "pair-b", me, challenge["from"], nonce, challenge["nonce"])
        await ws.send(model.encode({"type": "pair_proof", "mac": proof}))
        with pytest.raises(Exception):  # noqa: B017, PT011 — chiusa, senza ``pair_ok``
            await asyncio.wait_for(ws.recv(), timeout=5)
        assert a.peers() == []
    finally:
        for ws, *_rest in conns:
            await ws.close()


# -- Le risposte -----------------------------------------------------------------------


async def test_a_question_interrupted_by_a_restart_is_answered_failed(two):
    a, b = two
    await pair(a, b)
    delivered: list[Any] = []

    async def deliver(msg: Any) -> None:
        delivered.append(msg)

    asking = PeerQuestions(_link(a), a.workspace, get_agent=lambda: None, deliver=deliver)
    asked = await asking.ask("", "sono arrivati i documenti?")
    await wait_until(lambda: _link(a).store.queued_count(a.peers()[0].id) == 0)
    # Su ``b`` nessuno risponde: la riga resta com'è dopo un gateway fermato a metà.
    peer_on_b = b.peers()[0].id
    _link(b).store.add_question(peer_on_b, "in", asked["qid"], "…", state="answering")

    answering = PeerQuestions.__new__(PeerQuestions)
    answering._link = _link(b)
    answering._answering = set()
    assert await answering.recover_interrupted() == 1

    row = _link(b).store.question(peer_on_b, "in", asked["qid"])
    assert row is not None and row["state"] == "failed"
    await wait_until(lambda: bool(delivered))
    card = delivered[0].metadata["_agent_ui"]["peer_answer"]
    assert card["status"] == "failed"
    # Una seconda passata non la ridice.
    assert await answering.recover_interrupted() == 0


async def test_a_question_being_answered_now_is_not_taken_for_an_interrupted_one(two):
    a, b = two
    await pair(a, b)
    peer_on_b = b.peers()[0].id
    _link(b).store.add_question(peer_on_b, "in", QID, "…", state="answering")
    answering = PeerQuestions.__new__(PeerQuestions)
    answering._link = _link(b)
    answering._answering = {QID}

    assert await answering.recover_interrupted() == 0
    row = _link(b).store.question(peer_on_b, "in", QID)
    assert row is not None and row["state"] == "answering"


async def test_an_answer_whose_card_could_not_be_shown_is_shown_on_the_next_arrival(two):
    a, b = two
    await pair(a, b)
    peer = a.peers()[0].id
    attempts = 0
    shown: list[Any] = []

    async def deliver(msg: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("the chat was not there for a moment")
        shown.append(msg)

    asking = PeerQuestions(_link(a), a.workspace, get_agent=lambda: None, deliver=deliver)
    asked = await asking.ask("", "sono arrivati i documenti?")
    answer = {"qid": asked["qid"], "status": "ok", "text": "Sì, stamattina.", "files": []}

    with pytest.raises(RuntimeError):
        await asking._on_answer(peer, dict(answer))
    row = _link(a).store.question(peer, "out", asked["qid"])
    assert row is not None and row["state"] == "queued"

    await asking._on_answer(peer, dict(answer))
    row = _link(a).store.question(peer, "out", asked["qid"])
    assert row is not None and row["state"] == "answered"
    assert len(shown) == 1
    # E da qui un altro arrivo è un doppione.
    await asking._on_answer(peer, dict(answer))
    assert len(shown) == 1


# -- La notifica «Consenti» --------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "it"])
def test_the_confirmation_says_the_whole_address_in_its_body(language: str):
    host = "www.banca-esempio.it." + "accesso-sicuro." * 8 + "evil.example"
    what = say("peer.what_open", language, target=host)
    body = say("peer.confirm_body", language, peer="Personale", what=what)
    # Il titolo Android è una riga: il sito intero sta nel corpo, che va a capo.
    assert host in body
    assert "Personale" in body
