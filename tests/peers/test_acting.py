"""Le richieste di fare fra due Jenny: il permesso, il turno, l'elenco, l'annulla.

Due Jenny vere sul filo (``harness.Jenny``); l'agente di chi fa è un finto che
scrive un file nel registro della richiesta, come farebbe il loop vero, e
risponde a copione. Il cancello sulle uscite e le conferme si provano a parte,
con un'autorità finta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny import db as jdb
from jenny.agent.subagent_records import SubagentSpec
from jenny.agent.write_journal import journal_for, journal_key
from jenny.config.loader import load_config, save_config
from jenny.peers import gate
from jenny.peers.acting import PeerActs
from jenny.peers.confirm import Confirmations
from jenny.peers.questions import PeerQuestions
from jenny.session.keys import is_internal_session_key, is_peer_act_session_key
from tests.peers.harness import Jenny, pair
from tests.support.aio import wait_until


@pytest.fixture(autouse=True)
def _no_authority():
    yield
    gate.install(None)
    jdb.close_all()


class FakeSubagents:
    """Il gestore degli aiutanti, per quel che ``PeerActs.working`` gli chiede."""

    def __init__(self) -> None:
        self.running: dict[str, tuple[str, SubagentSpec]] = {}

    def start(self, tid: str, session_key: str, act: dict[str, str]) -> None:
        self.running[tid] = (session_key, SubagentSpec(task="t", label="t", peer_act=act))

    def get_running_ids_by_session(self, session_key: str) -> frozenset[str]:
        return frozenset(t for t, (k, _s) in self.running.items() if k == session_key)

    def _spec_for_task(self, tid: str) -> SubagentSpec | None:
        entry = self.running.get(tid)
        return entry[1] if entry else None


class ActingAgent:
    """Fa la richiesta: scrive *path* nel registro della richiesta, e risponde."""

    def __init__(self, workspace: Path, reply: str = "Fatto: promemoria per domani alle 9.",
                 write: str | None = "note/bianchi.md") -> None:
        self.workspace = workspace
        self.reply = reply
        self.write = write
        self.calls: list[dict[str, Any]] = []
        self.sessions = MagicMock()
        self.subagents = FakeSubagents()

    async def process_direct_outcome(self, prompt: str, **kwargs: Any):
        self.calls.append({"prompt": prompt, **kwargs})
        act = gate.act_of(kwargs["metadata"])
        assert act is not None
        if self.write:
            # Quel che fa il loop: il registro ``peer-<qid>`` legato al turno.
            journal = journal_for(self.workspace, journal_key("peer", act["qid"]))
            target = self.workspace / self.write
            target.parent.mkdir(parents=True, exist_ok=True)
            journal.touch(target)
            target.write_text("richiamare Bianchi\n", encoding="utf-8")
            journal.settle("write_file")
        return SimpleNamespace(final_text=self.reply)


def _set_peer(j: Jenny, **fields: Any) -> None:
    cfg = load_config(j.config_path)
    for p in cfg.peers.peers:
        for key, value in fields.items():
            setattr(p, key, value)
    save_config(cfg, j.config_path)


def _wire(j: Jenny, agent: Any = None) -> tuple[PeerQuestions, PeerActs, list[Any]]:
    delivered: list[Any] = []

    async def deliver(msg):
        delivered.append(msg)

    q = PeerQuestions(j.link, j.workspace, get_agent=lambda: agent, deliver=deliver)
    acts = PeerActs(j.link, j.workspace, get_agent=lambda: agent,
                    confirmations=Confirmations(ttl_s=2))
    acts.deliver_card = q._deliver_card
    q.acts = acts
    j.link.acts = acts
    return q, acts, delivered


def _card(msg) -> dict[str, Any]:
    return msg.metadata["_agent_ui"]["peer_answer"]


async def test_off_refuses_without_a_turn(two):
    a, b = two
    await pair(a, b)
    qa, _, delivered = _wire(a)
    agent = ActingAgent(b.workspace)
    _wire(b, agent)

    await qa.ask("", "Mettimi un promemoria per richiamare Bianchi", mode="act")
    await wait_until(lambda: delivered, timeout=10)
    card = _card(delivered[0])
    assert card["status"] == "refused"
    assert card["mode"] == "act"
    assert agent.calls == []


async def test_a_request_runs_in_its_own_session_and_lists_what_it_touched(two):
    a, b = two
    await pair(a, b)
    _set_peer(b, act="confirm")
    qa, _, delivered = _wire(a)
    agent = ActingAgent(b.workspace)
    _wire(b, agent)

    asked = await qa.ask("", "Salvami una nota su Bianchi", mode="act")
    await wait_until(lambda: delivered, timeout=10)

    call = agent.calls[0]
    assert is_peer_act_session_key(call["session_key"])
    assert is_internal_session_key(call["session_key"])
    assert call["session_key"] == f"peer:{a.id}:act"
    assert call["metadata"]["_taint"] == ["peer:Personale"]
    assert call["metadata"][gate.PEER_ACT_META] == {"peer": a.id, "qid": asked["qid"]}
    # Non in sola lettura, e senza una vista chiusa: gli strumenti li sceglie il loop.
    assert "readonly" not in call["metadata"]
    assert "tools" not in call
    assert "BEGIN QUESTION\nSalvami" in call["prompt"]
    assert "waits for the user of this phone" in call["prompt"]

    card = _card(delivered[0])
    assert card["status"] == "ok"
    assert card["mode"] == "act"
    assert card["working"] is False
    assert card["qid"] == asked["qid"]
    assert card["peerId"] == b.id
    assert {"kind": "file", "label": "note/bianchi.md", "undoable": True} in card["done"]


async def test_undo_asked_from_the_other_phone_puts_the_file_back(two):
    a, b = two
    await pair(a, b)
    _set_peer(b, act="free")
    qa, acts_a, delivered = _wire(a)
    note = b.workspace / "note" / "bianchi.md"
    note.parent.mkdir(parents=True)
    note.write_text("prima\n", encoding="utf-8")
    _wire(b, ActingAgent(b.workspace))

    asked = await qa.ask("", "Aggiorna la nota su Bianchi", mode="act")
    await wait_until(lambda: delivered, timeout=10)
    assert note.read_text(encoding="utf-8") == "richiamare Bianchi\n"

    await acts_a.request_undo(b.id, asked["qid"])
    await wait_until(lambda: len(delivered) >= 2, timeout=10)
    card = _card(delivered[1])
    assert card["status"] == "undone"
    assert card["undo"]["restored"] == 1
    assert note.read_text(encoding="utf-8") == "prima\n"


async def test_undo_from_the_other_phone_is_refused_once_the_permission_is_off(two):
    a, b = two
    await pair(a, b)
    _set_peer(b, act="free")
    qa, acts_a, delivered = _wire(a)
    _, acts_b, _ = _wire(b, ActingAgent(b.workspace))

    asked = await qa.ask("", "Salvami una nota", mode="act")
    await wait_until(lambda: delivered, timeout=10)
    _set_peer(b, act="off")
    await acts_b.refresh()

    await acts_a.request_undo(b.id, asked["qid"])
    await wait_until(lambda: len(delivered) >= 2, timeout=10)
    assert _card(delivered[1])["status"] == "undo_refused"
    assert (b.workspace / "note" / "bianchi.md").exists()


async def test_a_request_that_left_helpers_answers_again_when_the_last_one_is_back(two):
    a, b = two
    await pair(a, b)
    _set_peer(b, act="free")
    qa, _, delivered = _wire(a)
    agent = ActingAgent(b.workspace, reply="Ho lanciato un aiutante.", write=None)
    _, acts_b, _ = _wire(b, agent)

    # L'aiutante parte dentro il turno: il finto lo annuncia prima di rispondere.
    original = agent.process_direct_outcome

    async def with_helper(prompt: str, **kwargs: Any):
        act = gate.act_of(kwargs["metadata"])
        acts_b.helper_started(act, "helper-lin1", "lin1")
        agent.subagents.start("t1", kwargs["session_key"], act)
        return await original(prompt, **kwargs)

    agent.process_direct_outcome = with_helper  # type: ignore[method-assign]

    asked = await qa.ask("", "Prepara il riassunto del contratto", mode="act")
    await wait_until(lambda: delivered, timeout=10)
    first = _card(delivered[0])
    assert first["working"] is True

    # Un turno che non è il rientro di un aiutante non chiude niente.
    meta = {gate.PEER_ACT_META: {"peer": a.id, "qid": asked["qid"]}}
    await acts_b.turn_ended(meta, "parola a caso")
    # Un aggiornamento avrebbe cambiato la riga prima di tornare: è ancora al lavoro.
    assert b.link.store.question(a.id, "in", asked["qid"])["state"] == "working"

    # Il rientro, mentre l'aiutante è ancora vivo: niente ancora.
    meta.update({"injected_event": "subagent_result", "subagent_lineage_id": "lin1"})
    await acts_b.turn_ended(meta, "presto")
    assert b.link.store.question(a.id, "in", asked["qid"])["state"] == "working"
    # Finito: il rientro chiude la richiesta.
    agent.subagents.running.clear()
    await acts_b.turn_ended(meta, "Il riassunto è nel quaderno Clienti.")
    await wait_until(lambda: len(delivered) >= 2, timeout=10)
    second = _card(delivered[1])
    assert second["working"] is False
    assert second["text"] == "Il riassunto è nel quaderno Clienti."
    row = b.link.store.question(a.id, "in", asked["qid"])
    assert "helper-lin1" in row["journals"]


async def test_undo_waits_for_the_helpers(two):
    a, b = two
    await pair(a, b)
    _set_peer(b, act="free")
    qa, _, delivered = _wire(a)
    agent = ActingAgent(b.workspace)
    _, acts_b, _ = _wire(b, agent)
    asked = await qa.ask("", "Salvami una nota", mode="act")
    await wait_until(lambda: delivered, timeout=10)
    act = {"peer": a.id, "qid": asked["qid"]}
    acts_b.helper_started(act, "helper-lin2", "lin2")
    agent.subagents.start("t2", f"peer:{a.id}:act", act)
    from jenny.peers.link import PeerError

    with pytest.raises(PeerError) as err:
        await acts_b.undo(a.id, asked["qid"])
    assert err.value.code == "still_working"


# -- Il cancello ---------------------------------------------------------------------------


class FakeAuthority:
    def __init__(self, level: str = "confirm", answer: bool = True) -> None:
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


ACT = {"peer": "a" * 32, "qid": "b" * 32}


async def test_outside_a_request_the_gate_does_nothing():
    gate.install(FakeAuthority(level="off"))
    assert await gate.before_tool("web_fetch", {"url": "https://x.example"}) is None
    assert gate.network_refusal() is None


async def test_confirm_asks_once_per_host_and_refuses_on_no():
    auth = FakeAuthority(level="confirm", answer=True)
    gate.install(auth)
    with gate.entered(ACT):
        assert await gate.before_tool("web_fetch", {"url": "https://Bianchi.IT/a"}) is None
        assert await gate.before_tool("web_fetch", {"url": "https://bianchi.it/b"}) is None
        assert auth.asked == [("open", "bianchi.it")]
        auth.answer = False
        refusal = await gate.before_tool("download_file", {"url": "https://evil.example/x"})
        assert refusal is not None and refusal.startswith("Refused")
        assert auth.asked[-1] == ("download", "evil.example")
        # La rete dal codice è chiusa, e il rifiuto dice dove andare.
        assert "web_fetch" in (gate.network_refusal() or "")
        # Il resto passa: scrivere, lanciare aiutanti, parlare all'utente.
        for name in ("write_file", "spawn", "message", "web_search", "cron"):
            assert await gate.before_tool(name, {}) is None


async def test_free_passes_and_notes_what_left():
    auth = FakeAuthority(level="free")
    gate.install(auth)
    with gate.entered(ACT):
        assert await gate.before_tool("web_fetch", {"url": "https://bianchi.it"}) is None
        assert gate.network_refusal() is None
        assert await gate.before_tool("write_stdin", {}) is None
    assert auth.asked == []
    assert auth.notes == [
        {"kind": "egress", "label": "bianchi.it", "undoable": False, "allowed": True}]


async def test_off_and_never_refuse():
    gate.install(FakeAuthority(level="off"))
    with gate.entered(ACT):
        assert await gate.before_tool("web_fetch", {"url": "https://bianchi.it"})
        assert await gate.before_tool("browser_do", {})
        assert gate.network_refusal()
    gate.install(FakeAuthority(level="free"))
    with gate.entered(ACT):
        assert await gate.before_tool("install_update", {})


async def test_without_an_authority_everything_out_is_closed():
    gate.install(None)
    with gate.entered(ACT):
        assert await gate.before_tool("web_fetch", {"url": "https://bianchi.it"})


def test_act_of_validates_the_ids():
    assert gate.act_of({gate.PEER_ACT_META: ACT}) == ACT
    assert gate.act_of({gate.PEER_ACT_META: {"peer": "x", "qid": "b" * 32}}) is None
    assert gate.act_of({}) is None
    assert gate.act_of(None) is None


def test_a_helper_spec_keeps_the_request_across_a_restart():
    spec = SubagentSpec(task="riassumi il contratto", label="contratto", peer_act=dict(ACT))
    again = SubagentSpec.from_dict(spec.to_dict())
    assert again.peer_act == ACT
    assert SubagentSpec.from_dict({**spec.to_dict(), "peer_act": {"peer": "x"}}).peer_act is None


# -- Le conferme ---------------------------------------------------------------------------


async def test_a_confirmation_is_answered_once_and_cleared():
    posted: list[tuple] = []
    cleared: list[str] = []
    conf = Confirmations(post=lambda *a: posted.append(a), clear=cleared.append, ttl_s=5)
    task = asyncio.create_task(conf.ask(peer="p" * 32, peer_name="Personale",
                                        qid="q" * 32, what="aprire bianchi.it"))
    await wait_until(lambda: conf.pending(), timeout=2)
    cid = conf.pending()[0]["cid"]
    assert posted == [(cid, "Personale", "aprire bianchi.it")]
    assert conf.resolve(cid, True) is True
    assert await task is True
    assert conf.resolve(cid, False) is False
    assert cleared == [cid]
    assert conf.pending() == []


async def test_an_unanswered_confirmation_is_a_no():
    conf = Confirmations(ttl_s=0.05)
    assert await conf.ask(peer="p" * 32, peer_name="P", qid="q" * 32, what="x") is False


async def test_a_confirmation_expires_on_the_real_clock(monkeypatch):
    # Il telefono che dorme: il tempo di asyncio quasi non passa, l'ora vera sì.
    # Prima la scadenza era un solo ``wait_for`` sul tempo del loop, e su
    # jenny_square una conferma era ancora in attesa dopo 684 s su 600.
    import jenny.peers.confirm as confirm_mod

    clock = {"ms": 1_000_000}
    monkeypatch.setattr(confirm_mod, "now_ms", lambda: clock["ms"])
    monkeypatch.setattr(confirm_mod, "CHECK_EVERY_S", 0.01)
    cleared: list[str] = []
    conf = Confirmations(clear=cleared.append, ttl_s=600)
    task = asyncio.create_task(conf.ask(peer="p" * 32, peer_name="P", qid="q" * 32, what="x"))
    await wait_until(lambda: conf.pending(), timeout=2)
    await asyncio.sleep(0.05)
    assert not task.done()
    clock["ms"] += 601_000
    assert await asyncio.wait_for(task, 2) is False
    assert conf.pending() == [] and len(cleared) == 1


async def test_the_peers_round_closes_a_confirmation_that_expired_in_sleep():
    conf = Confirmations(ttl_s=600)
    task = asyncio.create_task(conf.ask(peer="p" * 32, peer_name="P", qid="q" * 32, what="x"))
    await wait_until(lambda: conf.pending(), timeout=2)
    at = conf.pending()[0]["at_ms"]
    assert conf.expire_due(now=at + 599_000) == 0
    assert conf.expire_due(now=at + 600_000) == 1
    assert await asyncio.wait_for(task, 2) is False
    assert conf.pending() == []


async def test_the_container_round_expires_confirmations():
    from types import SimpleNamespace

    from jenny.runtime.container import GatewayContainer

    conf = Confirmations(ttl_s=600)
    task = asyncio.create_task(conf.ask(peer="p" * 32, peer_name="P", qid="q" * 32, what="x"))
    await wait_until(lambda: conf.pending(), timeout=2)
    conf._pending[conf.pending()[0]["cid"]].at_ms -= 601_000
    woken: list[bool] = []
    link = SimpleNamespace(wake=lambda: woken.append(True),
                           acts=SimpleNamespace(confirmations=conf))
    GatewayContainer._wake_peers(SimpleNamespace(_peer_link=lambda: link))  # type: ignore[arg-type]
    assert woken == [True]
    assert await asyncio.wait_for(task, 2) is False


async def test_turning_the_permission_off_denies_what_waits():
    conf = Confirmations(ttl_s=5)
    task = asyncio.create_task(conf.ask(peer="p" * 32, peer_name="P", qid="q" * 32, what="x"))
    await wait_until(lambda: conf.pending(), timeout=2)
    assert conf.deny_peer("p" * 32) == 1
    assert await task is False


async def test_a_helper_that_finished_inside_the_turn_leaves_nothing_working(two):
    """Il risultato entrato nel turno stesso: nessun rientro a parte, la risposta è finale."""
    a, b = two
    await pair(a, b)
    _set_peer(b, act="free")
    qa, _, delivered = _wire(a)
    agent = ActingAgent(b.workspace, reply="Fatto, l'aiutante ha finito.", write=None)
    _, acts_b, _ = _wire(b, agent)
    original = agent.process_direct_outcome

    async def helper_already_done(prompt: str, **kwargs: Any):
        acts_b.helper_started(gate.act_of(kwargs["metadata"]), "helper-lin3", "lin3")
        return await original(prompt, **kwargs)

    agent.process_direct_outcome = helper_already_done  # type: ignore[method-assign]
    await qa.ask("", "Fai una cosa veloce", mode="act")
    await wait_until(lambda: delivered, timeout=10)
    assert _card(delivered[0])["working"] is False


def test_a_phone_that_already_had_questions_gets_the_act_columns(tmp_path):
    """Il passo delle domande c'era già (la PR delle domande installata): arriva il nuovo."""
    from dataclasses import replace

    from jenny.peers.store import SCHEMA, PeerStore

    ws = tmp_path / "workspace"
    ws.mkdir()
    old = replace(SCHEMA, steps=SCHEMA.steps[:2])
    jdb.store_database(ws, jdb.STATE, old)
    store = PeerStore(ws)
    assert store.add_question("p" * 32, "in", "q" * 32, "fai", state="answering", mode="act")
    row = store.question("p" * 32, "in", "q" * 32)
    assert row["mode"] == "act" and row["done"] == [] and row["journals"] == []
    store.add_journal("p" * 32, "in", "q" * 32, "helper-x")
    assert store.find_question("in", "q" * 32)["journals"] == ["helper-x"]
