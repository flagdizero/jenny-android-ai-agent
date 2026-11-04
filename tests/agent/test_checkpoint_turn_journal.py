"""Il turno in volo sta nel diario del turno, non nei metadati della sessione.

Il checkpoint si scrive due volte per iterazione, e i metadati della sessione
sono una riga sola che si riscrive intera. Con le iterazioni già chiuse del
turno dentro il checkpoint, un turno di *n* iterazioni riscriveva ogni
risultato di tool circa *n* volte: I/O quadratico nella lunghezza del turno,
con un tetto di 200 iterazioni. Il diario riceve in append solo i messaggi nuovi.
"""

from __future__ import annotations

import asyncio
import sqlite3

from jenny.bus.events import InboundMessage
from jenny.providers.base import LLMResponse, ToolCallRequest
from jenny.session import store as session_store
from jenny.session.manager import SessionManager
from tests.support.agent import make_loop, make_provider
from tests.support.aio import wait_until

KEY = "unified:default"
STEPS = 12
RESULT_CHARS = 6000


def _steps_then_hang(step: dict, hang: asyncio.Event, steps: int):
    async def chat(**_kw):
        step["n"] += 1
        if step["n"] <= steps:
            return LLMResponse(
                content=f"passo {step['n']}", finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(id=f"c{step['n']}", name="read_file",
                                            arguments={"path": f"f{step['n']}.txt"})],
            )
        await hang.wait()
        return LLMResponse(content="mai")

    return chat


async def _start(tmp_path, monkeypatch, steps: int = STEPS):
    for i in range(1, steps + 1):
        (tmp_path / f"f{i}.txt").write_text(chr(ord("a") + i % 26) * RESULT_CHARS, "utf-8")
    saves: list[int] = []
    original = session_store.save

    def counting(database, key, **kwargs):  # type: ignore[no-untyped-def]
        if key == KEY:
            saves.append(len(kwargs["metadata"]))
        return original(database, key, **kwargs)

    monkeypatch.setattr(session_store, "save", counting)
    provider = make_provider()
    step = {"n": 0}
    hang = asyncio.Event()
    chat = _steps_then_hang(step, hang, steps)
    provider.chat_with_retry = chat
    provider.chat_stream_with_retry = chat
    loop = make_loop(tmp_path, provider=provider, max_iterations=steps + 5)
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="leggi")
    task = asyncio.create_task(loop._dispatch(msg))
    loop._active_tasks.setdefault(KEY, []).append(task)
    await wait_until(lambda: step["n"] >= steps + 1, timeout=10.0)
    return loop, task, saves


async def _stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_checkpoint_saves_do_not_grow_with_the_turn(tmp_path, monkeypatch):
    loop, task, saves = await _start(tmp_path, monkeypatch)
    try:
        saved = SessionManager(tmp_path).read_session_metadata(KEY)
        checkpoint = saved["metadata"][loop._RUNTIME_CHECKPOINT_KEY]
        assert "prior_messages" not in checkpoint
        assert checkpoint["prior_journal"]["count"] == 2 * (STEPS - 1)
        # Ogni salvataggio del turno porta al più l'iterazione in volo: il
        # primo e l'ultimo differiscono di un risultato, non di undici.
        assert len(saves) >= 2 * STEPS
        assert max(saves) - min(saves) < 2 * RESULT_CHARS
    finally:
        await _stop(task)


async def test_a_kill_restores_the_whole_turn_from_the_journal(tmp_path, monkeypatch):
    loop, task, _saves = await _start(tmp_path, monkeypatch)
    try:
        # Un processo nuovo: niente in memoria, solo il database.
        fresh = make_loop(tmp_path, provider=make_provider())
        session = SessionManager(tmp_path).get_or_create(KEY)
        assert fresh._restore_runtime_checkpoint(session)

        ids = [tc["id"] for m in session.messages for tc in (m.get("tool_calls") or [])]
        assert ids == [f"c{i}" for i in range(1, STEPS + 1)]
        results = [m for m in session.messages if m.get("role") == "tool"]
        assert len(results) == STEPS
        assert all(len(str(m["content"])) >= RESULT_CHARS for m in results)
        assert fresh.sessions.turn_read(KEY) == (None, [])
    finally:
        await _stop(task)


async def test_a_journal_from_another_turn_is_not_restored(tmp_path, monkeypatch):
    loop, task, _saves = await _start(tmp_path, monkeypatch, steps=3)
    try:
        with loop.sessions._db.write() as conn:
            conn.execute("UPDATE session_turns SET stamp = 'a-different-turn' WHERE key = ?",
                         (KEY,))

        session = SessionManager(tmp_path).get_or_create(KEY)
        assert loop._restore_runtime_checkpoint(session)

        # Solo l'iterazione in volo: la terza, con la sua tool call.
        ids = [tc["id"] for m in session.messages for tc in (m.get("tool_calls") or [])]
        assert ids == ["c3"]
    finally:
        await _stop(task)


async def test_an_unwritable_journal_keeps_the_turn_inline(tmp_path, monkeypatch):
    def broken(self, key, stamp, lines):  # type: ignore[no-untyped-def]
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(SessionManager, "turn_reset", broken)
    loop, task, _saves = await _start(tmp_path, monkeypatch, steps=3)
    try:
        checkpoint = loop.sessions.get_or_create(KEY).metadata[loop._RUNTIME_CHECKPOINT_KEY]
        assert "prior_journal" not in checkpoint
        assert len(checkpoint["prior_messages"]) == 4

        session = SessionManager(tmp_path).get_or_create(KEY)
        assert loop._restore_runtime_checkpoint(session)
        ids = [tc["id"] for m in session.messages for tc in (m.get("tool_calls") or [])]
        assert ids == ["c1", "c2", "c3"]
    finally:
        await _stop(task)
