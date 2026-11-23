"""La linea passata fra due Jenny, da capo a fondo.

Due Jenny vere sul filo (``harness.Jenny``): ``a`` passa la sua chat, ``b`` la
riceve. Quel che sta attorno è finto e registra: i frame per la WebUI di ``a``,
i messaggi che entrano nella chat di ``b``, le schede. Si prova quel che il
codice decide: il permesso, dove entra il messaggio, cosa torna indietro, cosa
resta su ``a`` alla chiusura, un salto solo.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.tools.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from jenny.agent.tools.peer import LineMoveTool, PeerAskTool, PeerLineTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.bus.events import OUTBOUND_META_AGENT_UI
from jenny.peers.line import LINE_ID_META, LINE_PEER_META, PeerLines, out_key
from jenny.peers.link import PeerError
from jenny.runtime.delivery import RECORD_TAINT_META
from tests.peers.harness import Jenny, pair
from tests.support.aio import wait_until

WS = "websocket"
CHAT = "default"


async def _until(check: Any, *, timeout: float = 10.0) -> None:
    """Come ``wait_until``, per una condizione che va attesa (legge lo stato su disco)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await check():
        if loop.time() >= deadline:
            raise AssertionError(f"condizione non raggiunta in {timeout}s")
        await asyncio.sleep(0.01)


class FakeAgent:
    """Scrive il riassunto a copione e ricorda come è stato chiamato."""

    def __init__(self, reply: str = "Avete parlato del fornitore. Ho risposto io.") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []
        self.tools = ToolRegistry()

    async def process_direct_outcome(self, prompt: str, **kwargs: Any):
        self.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(final_text=self.reply)


class Side:
    """Una Jenny col suo ``PeerLines`` e quel che ha visto passare."""

    def __init__(self, jenny: Jenny, *, places: list[str] | None = None,
                 agent: FakeAgent | None = None) -> None:
        self.jenny = jenny
        self.frames: list[dict[str, Any]] = []
        self.injected: list[dict[str, Any]] = []
        self.cards: list[Any] = []
        self.agent = agent
        self.places = list(places or [])

        async def emit(channel: str, chat_id: str, frame: dict[str, Any]) -> None:
            self.frames.append({"channel": channel, "chat_id": chat_id, **frame})

        async def inject(chat_id: str, text: str, origin: dict[str, str],
                         metadata: dict[str, Any]) -> None:
            self.injected.append(
                {"chat_id": chat_id, "text": text, "origin": origin, "metadata": metadata}
            )

        async def deliver(msg: Any) -> None:
            self.cards.append(msg)

        self.lines = PeerLines(
            jenny.link, jenny.workspace, get_agent=lambda: self.agent,
            deliver=deliver, emit=emit, inject=inject, places=lambda: list(self.places),
        )

    def kinds(self) -> list[str]:
        return [f["kind"] for f in self.frames]

    def card(self, name: str) -> dict[str, Any] | None:
        for msg in self.cards:
            ui = (msg.metadata or {}).get(OUTBOUND_META_AGENT_UI) or {}
            if name in ui:
                return ui[name]
        return None


async def _linked(two, *, allow: bool = True, **b_kwargs: Any) -> tuple[Side, Side]:
    a, b = two
    await pair(a, b)
    side_a, side_b = Side(a), Side(b, **b_kwargs)
    if allow:
        await b.link.set_permissions(a.id, line=True)
    return side_a, side_b


async def _open(side_a: Side, side_b: Side, place: str = "") -> dict[str, Any]:
    await side_a.lines.open(WS, CHAT, side_b.jenny.id, place)

    async def is_open() -> bool:
        line = await side_a.lines.status(WS, CHAT)
        return line is not None and line["state"] == "open"

    await _until(is_open)
    line = await side_a.lines.status(WS, CHAT)
    assert line is not None
    return line


async def test_without_the_permission_the_line_is_refused(two):
    side_a, side_b = await _linked(two, allow=False)
    await side_a.lines.open(WS, CHAT, side_b.jenny.id)

    await wait_until(lambda: "closed" in side_a.kinds())

    closed = next(f for f in side_a.frames if f["kind"] == "closed")
    assert closed["reason"] == "not_allowed"
    assert await side_a.lines.status(WS, CHAT) is None
    assert side_b.injected == []
    # Niente detto, niente segnaposto.
    assert side_a.card("line_stub") is None


async def test_what_the_user_writes_enters_the_other_jennys_chat(two):
    side_a, side_b = await _linked(two)
    await _open(side_a, side_b)

    assert await side_a.lines.route(WS, CHAT, "ci sono email nuove?") is True
    await wait_until(lambda: len(side_b.injected) == 1)

    got = side_b.injected[0]
    assert got["chat_id"] == "default"
    assert got["text"] == "ci sono email nuove?"
    assert got["origin"] == {"origin": "peer_line", "device": "Personale"}
    assert got["metadata"][LINE_PEER_META] == side_a.jenny.id
    # Su ``b`` la riga dice da quando l'altra ha la linea.
    assert side_b.card("line_in")["event"] == "open"


async def test_the_final_reply_goes_back_and_the_pieces_do_not(two):
    side_a, side_b = await _linked(two)
    await _open(side_a, side_b)
    await side_a.lines.route(WS, CHAT, "ciao")
    await wait_until(lambda: len(side_b.injected) == 1)
    meta = side_b.injected[0]["metadata"]

    def out(text: str, **flags: Any) -> Any:
        return SimpleNamespace(channel=WS, content=text, metadata={**meta, **flags})

    await side_b.lines.on_outbound(out("Du", _stream_delta=True))
    await side_b.lines.on_outbound(out("sto guardando", _progress=True))
    await side_b.lines.on_outbound(out("", _turn_end=True))
    await side_b.lines.on_outbound(out("Due, di stamattina.", _streamed=True))

    def replies() -> list[str]:
        return [f["item"]["text"] for f in side_a.frames
                if f["kind"] == "item" and f["item"]["role"] == "peer"]

    await wait_until(lambda: replies() == ["Due, di stamattina."])
    line = await side_a.lines.status(WS, CHAT)
    assert [(i["role"], i["text"]) for i in line["items"]] == [
        ("user", "ciao"), ("peer", "Due, di stamattina."),
    ]


async def test_a_reply_of_another_turn_is_not_sent(two):
    side_a, side_b = await _linked(two)
    await _open(side_a, side_b)
    # Un turno di ``b`` che non arriva dalla linea: resta lì.
    await side_b.lines.on_outbound(SimpleNamespace(channel=WS, content="per chi sta qui", metadata={}))
    # E uno di una linea già chiusa: l'id non è quello aperto.
    stale = {LINE_PEER_META: side_a.jenny.id, LINE_ID_META: "0" * 32}
    await side_b.lines.on_outbound(SimpleNamespace(channel=WS, content="vecchio", metadata=stale))
    await side_a.lines.route(WS, CHAT, "ci sei?")
    await wait_until(lambda: len(side_b.injected) == 1)
    assert not [f for f in side_a.frames if f["kind"] == "item" and f["item"]["role"] == "peer"]


async def test_moving_to_a_notebook_changes_where_the_next_message_lands(two):
    side_a, side_b = await _linked(two, places=["Piante", "Casa"])
    await _open(side_a, side_b)

    await side_a.lines.move(WS, CHAT, "Piante")

    async def moved() -> bool:
        line = await side_a.lines.status(WS, CHAT)
        return line is not None and line["place"] == "Piante"

    await _until(moved)
    await side_a.lines.route(WS, CHAT, "quando ho bagnato il ficus?")
    await wait_until(lambda: len(side_b.injected) == 1)
    assert side_b.injected[0]["chat_id"] == "project:Piante"


async def test_a_notebook_that_is_not_there_leaves_the_line_where_it_was(two):
    side_a, side_b = await _linked(two, places=["Piante"])
    await _open(side_a, side_b)

    await side_a.lines.move(WS, CHAT, "Inesistente")

    await wait_until(lambda: any(f.get("note") == "no_place" for f in side_a.frames))
    line = await side_a.lines.status(WS, CHAT)
    assert line["place"] == ""


async def test_opening_straight_into_a_notebook(two):
    side_a, side_b = await _linked(two, places=["Fornitori"])
    line = await _open(side_a, side_b, "Fornitori")
    assert line["place"] == "Fornitori"


async def test_a_chat_without_a_line_is_not_routed(two):
    side_a, _side_b = await _linked(two)
    assert await side_a.lines.route(WS, CHAT, "ciao") is False
    assert await side_a.lines.route(WS, CHAT, "/torna") is False


async def test_coming_back_leaves_a_stub_and_the_other_jennys_two_lines(two):
    agent = FakeAgent()
    side_a, side_b = await _linked(two, agent=agent)
    await _open(side_a, side_b)
    await side_a.lines.route(WS, CHAT, "rispondi al fornitore che va bene")
    await wait_until(lambda: len(side_b.injected) == 1)

    assert await side_a.lines.route(WS, CHAT, "/torna") is True

    assert await side_a.lines.status(WS, CHAT) is None
    stub = side_a.card("line_stub")
    assert stub is not None and stub["peerId"] == side_b.jenny.id
    assert stub["to"] >= stub["from"]
    # Il messaggio dopo torna a essere per la Jenny di casa.
    assert await side_a.lines.route(WS, CHAT, "che ore sono?") is False

    await wait_until(lambda: side_a.card("line_trace") is not None)
    trace = side_a.card("line_trace")
    assert trace["text"] == agent.reply
    assert trace["lid"] == stub["lid"]
    # Il riassunto è dell'altra Jenny: nella storia di questa entra non fidato.
    traced = next(m for m in side_a.cards
                  if "line_trace" in (m.metadata.get(OUTBOUND_META_AGENT_UI) or {}))
    assert traced.metadata[RECORD_TAINT_META]
    # Il turno del riassunto non ha strumenti, e legge il filo come un dato.
    call = agent.calls[0]
    assert "rispondi al fornitore che va bene" in call["prompt"]
    assert call["tools"].get_definitions() == []
    assert side_b.card("line_in") is not None


async def test_the_trace_can_be_turned_off_on_the_phone_that_writes_it(two):
    agent = FakeAgent()
    side_a, side_b = await _linked(two, agent=agent)
    await side_b.jenny.link.set_permissions(side_a.jenny.id, line_trace=False)
    await _open(side_a, side_b)
    await side_a.lines.route(WS, CHAT, "ciao")
    await wait_until(lambda: len(side_b.injected) == 1)

    await side_a.lines.close(WS, CHAT)

    await wait_until(
        lambda: any((c.metadata.get(OUTBOUND_META_AGENT_UI) or {}).get("line_in", {})
                    .get("event") == "close" for c in side_b.cards)
    )
    assert agent.calls == []
    assert side_a.card("line_trace") is None


async def test_a_line_where_nothing_was_said_leaves_nothing(two):
    side_a, side_b = await _linked(two)
    await _open(side_a, side_b)
    await side_a.lines.close(WS, CHAT)
    assert side_a.card("line_stub") is None
    assert "closed" in side_a.kinds()


async def test_taking_the_permission_away_closes_the_line(two):
    side_a, side_b = await _linked(two)
    await _open(side_a, side_b)

    await side_b.jenny.link.set_permissions(side_a.jenny.id, line=False)

    await wait_until(lambda: "closed" in side_a.kinds())
    assert await side_a.lines.status(WS, CHAT) is None


async def test_the_other_jennys_chats_are_names_only(two):
    side_a, side_b = await _linked(two, places=["Piante", "Casa"])
    assert await side_a.lines.chats(side_b.jenny.id) == ["Piante", "Casa"]


async def test_the_chats_are_not_listed_without_the_permission(two):
    side_a, side_b = await _linked(two, allow=False, places=["Piante"])
    with pytest.raises(PeerError) as err:
        await side_a.lines.chats(side_b.jenny.id)
    assert err.value.code == "refused"


async def test_a_jenny_of_an_older_version_closes_the_line_with_its_reason(two):
    a, b = two
    await pair(a, b)
    side_a = Side(a)  # ``b`` non ha i gestori della linea: risponde «non lo conosco»

    await side_a.lines.open(WS, CHAT, b.id)

    await wait_until(lambda: "closed" in side_a.kinds())
    closed = next(f for f in side_a.frames if f["kind"] == "closed")
    assert closed["reason"] == "unsupported"


async def test_the_line_is_on_disk(two):
    side_a, side_b = await _linked(two)
    await _open(side_a, side_b)
    row = side_a.jenny.link.store.line("out", out_key(WS, CHAT))
    assert row is not None and row["peer"] == side_b.jenny.id
    assert side_b.jenny.link.store.line("in", side_a.jenny.id) is not None


# -- Gli strumenti -----------------------------------------------------------


def _bound(tool: Any, lines: Any, peers: list[Any] | None = None) -> Any:
    async def list_peers() -> list[Any]:
        return list(peers or [])

    tool.bind_questions(SimpleNamespace(lines=lines, peers=list_peers))
    return tool


def _ctx(**metadata: Any) -> RequestContext:
    return RequestContext(channel=WS, chat_id=CHAT, session_key="unified:default",
                          metadata=metadata)


async def test_the_tool_passes_the_chat_it_is_called_from(two):
    side_a, side_b = await _linked(two)
    tool = _bound(PeerLineTool(), side_a.lines)
    token = bind_request_context(_ctx())
    try:
        said = await tool.execute(peer="")
    finally:
        reset_request_context(token)
    assert "/torna" in said
    assert await side_a.lines.has_out(WS, CHAT)


async def test_one_hop_only(two):
    side_a, side_b = await _linked(two)
    # Su ``b`` il turno arriva da una linea: da lì non se ne apre un'altra,
    # e non si interroga una terza Jenny.
    from_line = _ctx(**{LINE_PEER_META: side_a.jenny.id})
    token = bind_request_context(from_line)
    try:
        passed = await _bound(PeerLineTool(), side_b.lines).execute(peer="")
        asked = await _bound(PeerAskTool(), side_b.lines).execute(question="che ore sono?")
    finally:
        reset_request_context(token)
    assert "third" in passed and "third" in asked
    assert not await side_b.lines.has_out(WS, CHAT)


async def test_the_line_is_not_passed_from_a_notebook_chat(two):
    side_a, _side_b = await _linked(two)
    ctx = RequestContext(channel=WS, chat_id="project:Piante", session_key="project:Piante")
    token = bind_request_context(ctx)
    try:
        said = await _bound(PeerLineTool(), side_a.lines).execute(peer="")
    finally:
        reset_request_context(token)
    assert "main chat" in said


async def test_moving_by_voice_works_only_inside_a_line(two):
    side_a, side_b = await _linked(two, places=["Piante"])
    await _open(side_a, side_b)
    tool = _bound(LineMoveTool(), side_b.lines)

    token = bind_request_context(_ctx())
    try:
        outside = await tool.execute(notebook="Piante")
    finally:
        reset_request_context(token)
    assert "Not available here" in outside

    token = bind_request_context(_ctx(**{LINE_PEER_META: side_a.jenny.id}))
    try:
        missing = await tool.execute(notebook="Orto")
        moved = await tool.execute(notebook="Piante")
    finally:
        reset_request_context(token)
    assert "Piante" in missing and "no notebook" in missing
    assert moved.startswith("Moved")

    async def there() -> bool:
        line = await side_a.lines.status(WS, CHAT)
        return line is not None and line["place"] == "Piante"

    await _until(there)


async def test_unlinking_brings_the_chat_back_home(two):
    side_a, side_b = await _linked(two)
    await _open(side_a, side_b)

    await side_a.jenny.link.remove(side_b.jenny.id)

    assert await side_a.lines.status(WS, CHAT) is None
    closed = next(f for f in side_a.frames if f["kind"] == "closed")
    assert closed["reason"] == "unpaired"
    await wait_until(lambda: side_b.jenny.link.store.line("in", side_a.jenny.id) is None,
                     timeout=10)


async def test_a_telegram_chat_can_be_passed_too(two):
    side_a, side_b = await _linked(two)
    await side_a.lines.open("telegram", "4242", side_b.jenny.id)

    async def is_open() -> bool:
        line = await side_a.lines.status("telegram", "4242")
        return line is not None and line["state"] == "open"

    await _until(is_open)
    assert await side_a.lines.route("telegram", "4242", "ci sei?") is True
    await wait_until(lambda: len(side_b.injected) == 1)
    # Un'altra chat dello stesso canale resta di questa Jenny.
    assert await side_a.lines.route("telegram", "9999", "ci sei?") is False
    assert await side_a.lines.route("telegram", "4242", "/torna") is True
    # Il segnaposto sta nella chat di sempre della WebUI, non su Telegram.
    stub = next(m for m in side_a.cards
                if "line_stub" in (m.metadata.get(OUTBOUND_META_AGENT_UI) or {}))
    assert (stub.channel, stub.chat_id) == ("websocket", "default")


async def test_a_line_can_only_be_passed_from_a_chat_channel(two):
    side_a, side_b = await _linked(two)
    with pytest.raises(PeerError) as err:
        await side_a.lines.open("notification", "native", side_b.jenny.id)
    assert err.value.code == "bad_channel"


async def test_the_tool_says_when_the_other_refused(two):
    # Il difetto visto col modello vero: l'altra rifiuta in un attimo, e Jenny
    # diceva lo stesso «ti passo Lavoro» e poi rispondeva lei.
    side_a, side_b = await _linked(two, allow=False)
    token = bind_request_context(_ctx())
    try:
        said = await _bound(PeerLineTool(), side_a.lines).execute(peer="")
    finally:
        reset_request_context(token)
    assert "NOT passed" in said and "does not take the line from this phone" in said
    assert "/torna" not in said
    assert not await side_a.lines.has_out(WS, CHAT)


async def test_the_tool_says_when_the_notebook_is_not_there(two):
    side_a, side_b = await _linked(two, places=["Piante"])
    token = bind_request_context(_ctx())
    try:
        said = await _bound(PeerLineTool(), side_a.lines).execute(peer="", notebook="Orto")
    finally:
        reset_request_context(token)
    assert "no notebook called 'Orto'" in said and "/torna" in said
    assert (await side_a.lines.status(WS, CHAT))["place"] == ""


async def test_an_open_nobody_answered_is_waiting(two):
    side_a, side_b = await _linked(two)
    from jenny.peers.line import out_key

    row_key = out_key(WS, CHAT)
    await side_a.lines._db(
        side_a.lines.store.line_put, "out", row_key,
        peer=side_b.jenny.id, lid="a" * 32, place="", state="opening",
    )
    assert await side_a.lines.settled(WS, CHAT, "a" * 32, timeout=0.05) == "waiting"
    assert await side_a.lines.settled(WS, CHAT, "b" * 32, timeout=0.05) == "closed"


def test_page_links_travel_as_plain_names():
    from jenny.peers.line import plain_links

    text = "Vedi [[piante/acero/concimazione]] e [[piante/ginkgo|il ginkgo]], non [questo](x)."
    assert plain_links(text) == "Vedi «acero/concimazione» e il ginkgo, non [questo](x)."
    assert plain_links("[[indice]]") == "«indice»"


async def test_after_a_restart_the_unsent_messages_are_back_in_the_thread(two):
    side_a, side_b = await _linked(two)
    await _open(side_a, side_b)
    lines = side_a.lines
    from jenny.peers.line import out_key

    row = await lines._db(lines.store.line, "out", out_key(WS, CHAT))
    # Un messaggio ancora in coda, e la memoria persa come dopo un riavvio.
    await lines._db(
        lines.store.enqueue, row["peer"], "line_say",
        {"lid": row["lid"], "mid": "c" * 32, "text": "ci sei?"},
    )
    lines._out_items.clear()
    lines._seen_out.clear()
    status = await lines.status(WS, CHAT)
    assert [(i["id"], i["role"], i["text"]) for i in status["items"]] == [
        ("c" * 32, "user", "ci sei?"),
    ]
    # La seconda lettura non lo raddoppia.
    assert len((await lines.status(WS, CHAT))["items"]) == 1


async def test_a_notebook_is_found_whatever_the_case(two):
    # «passami Lavoro sul quaderno fornitori», col quaderno che si chiama
    # «Fornitori»: sui due emulatori la linea si apriva sulla chat di sempre.
    side_a, side_b = await _linked(two, places=["Fornitori"])
    token = bind_request_context(_ctx())
    try:
        said = await _bound(PeerLineTool(), side_a.lines).execute(peer="", notebook="fornitori")
    finally:
        reset_request_context(token)
    assert "no notebook" not in said and "/torna" in said
    assert (await side_a.lines.status(WS, CHAT))["place"] == "Fornitori"
    # Lo stesso a parole, dentro la linea.
    await side_b.lines.move_here(side_a.jenny.id, "")
    assert await side_b.lines.move_here(side_a.jenny.id, "FORNITORI") is None
    row = await side_b.lines._db(side_b.lines.store.line, "in", side_a.jenny.id)
    assert row["place"] == "Fornitori"


def test_a_helper_spec_keeps_the_line_across_a_restart():
    from jenny.agent.subagent import SubagentSpec

    mark = {"peer": "a" * 32, "lid": "b" * 32}
    spec = SubagentSpec(task="leggi i sensori", label="sensori", peer_line=dict(mark))
    assert SubagentSpec.from_dict(spec.to_dict()).peer_line == mark
    assert SubagentSpec.from_dict({**spec.to_dict(), "peer_line": {"peer": "x"}}).peer_line is None
    assert SubagentSpec.from_dict({**spec.to_dict(), "peer_line": None}).peer_line is None


async def test_the_result_of_a_helper_launched_from_a_line_goes_back_on_it(tmp_path):
    # Sui due emulatori (23/11): «quali piante annaffio?» arrivato dalla linea,
    # Jenny lancia un aiutante e risponde «un attimo»; il risultato vero, in un
    # turno d'annuncio, restava su questo telefono e all'altra non arrivava.
    from unittest.mock import MagicMock

    from jenny.agent.subagent import SubagentManager, SubagentSpec
    from jenny.bus.queue import MessageBus
    from jenny.peers.line import LINE_ID_META, LINE_PEER_META, line_of

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus,
                          max_tool_result_chars=10_000)
    spec = SubagentSpec(task="leggi i sensori", label="sensori",
                        peer_line={"peer": "a" * 32, "lid": "b" * 32})
    mgr._spec_for_task = lambda task_id: spec  # type: ignore[method-assign]
    origin = {"channel": "websocket", "chat_id": "default", "session_key": "unified:default"}
    await mgr._announce_result("sub-1", "sensori", "leggi i sensori", "20%", origin, "ok")
    msg = await bus.consume_inbound()
    assert line_of(msg.metadata) == "a" * 32
    assert msg.metadata[LINE_PEER_META] == "a" * 32 and msg.metadata[LINE_ID_META] == "b" * 32


async def test_a_helper_launched_from_a_line_turn_takes_the_mark():
    from jenny.agent.tools.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from jenny.peers.line import LINE_ID_META, LINE_PEER_META, line_mark

    ctx = RequestContext(channel=WS, chat_id=CHAT, session_key="unified:default",
                         metadata={LINE_PEER_META: "a" * 32, LINE_ID_META: "b" * 32})
    token = bind_request_context(ctx)
    try:
        from jenny.agent.tools.context import current_request_context

        assert line_mark(current_request_context().metadata) == {"peer": "a" * 32, "lid": "b" * 32}
    finally:
        reset_request_context(token)
    assert line_mark({LINE_PEER_META: "a" * 32}) is None


async def test_the_markers_go_to_the_chat_where_the_line_is(two):
    side_a, side_b = await _linked(two, places=["Piante"])
    await _open(side_a, side_b, "Piante")
    await side_a.lines.close(WS, CHAT)
    await wait_until(lambda: len([
        c for c in side_b.cards if "line_in" in (c.metadata.get(OUTBOUND_META_AGENT_UI) or {})
    ]) == 2)
    markers = [
        (c.chat_id, (c.metadata.get(OUTBOUND_META_AGENT_UI) or {})["line_in"]["event"])
        for c in side_b.cards
        if "line_in" in (c.metadata.get(OUTBOUND_META_AGENT_UI) or {})
    ]
    assert markers == [("project:Piante", "open"), ("project:Piante", "close")]


def test_a_line_turn_tells_the_model_who_is_writing():
    from jenny.peers.line import LINE_ID_META, LINE_PEER_META, line_runtime_lines

    meta = {LINE_PEER_META: "a" * 32, LINE_ID_META: "b" * 32, "origin_device": "Personale"}
    [line] = line_runtime_lines(meta)
    assert '"Personale"' in line and "other phone" in line
    assert line_runtime_lines({"origin_device": "Personale"}) == []


async def test_a_superseded_reply_of_a_line_turn_reaches_the_other_jenny(tmp_path):
    from types import SimpleNamespace

    from jenny.agent.loop import AgentLoop
    from jenny.peers.line import LINE_ID_META, LINE_PEER_META, line_of

    sent: list[Any] = []

    async def on_outbound(msg: Any) -> None:
        sent.append(msg)

    loop = SimpleNamespace(peer_questions=SimpleNamespace(lines=SimpleNamespace(
        on_outbound=on_outbound)))
    meta = {LINE_PEER_META: "a" * 32, LINE_ID_META: "b" * 32}
    callback = AgentLoop._line_reply_callback(loop, WS, CHAT, meta)  # type: ignore[arg-type]
    assert callback is not None
    await callback("Cartiera Bianchi")
    assert [(m.chat_id, m.content, line_of(m.metadata)) for m in sent] == [
        (CHAT, "Cartiera Bianchi", "a" * 32),
    ]
    # Fuori da una linea non c'è niente da mandare.
    assert AgentLoop._line_reply_callback(loop, WS, CHAT, {}) is None  # type: ignore[arg-type]


async def test_a_change_of_linked_jennys_tells_the_house(two):
    # Sui due emulatori (23/11): scollegata dall'altro telefono, in casa il
    # tasto ⇄ e la riga «Altre Jenny» restavano col nome di prima.
    side_a, _side_b = await _linked(two)
    await side_a.lines.peers_changed()
    assert side_a.frames[-1] == {"channel": WS, "chat_id": CHAT, "kind": "peers"}
