"""Chi *produce* l'attivita viva di un subagent: hook, throttle, transizione finale.

La fase precedente ha costruito il pozzo (ring, formatter, digest); qui si
verifica che qualcuno ci versi qualcosa, e nei momenti giusti. Le asserzioni che
contano sono tre, e sono tutte su una granularita:

* una tool call produce **due** eventi (inizio e fine), non uno a fine iterazione;
* l'accoppiamento inizio/fine e esatto con tre chiamate dello stesso tool in volo
  — il caso osservato sul device, non un caso limite;
* il segnale di pensiero e coalescato: uno stream di ragionamento non deve poter
  riempire il ring e cacciarne fuori tutto il resto.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from jenny.agent.hook import AgentHookContext, ToolResultHookContext
from jenny.agent.runner import AgentRunResult
from jenny.agent.subagent import (
    _PHASE_LABELS,
    _THINKING_EXCERPT_CHARS,
    _THINKING_THROTTLE_S,
    SubagentManager,
    SubagentStatus,
    _SubagentHook,
    _thinking_excerpt,
)
from jenny.agent.subagent_activity import (
    DIGEST_KIND_TOOL,
    DIGEST_STATUS_INCOMPLETE,
    KIND_ERROR,
    KIND_ITERATION,
    KIND_MESSAGE_IN,
    KIND_PHASE,
    KIND_RESULT,
    KIND_THINKING,
    KIND_TOOL_END,
    KIND_TOOL_START,
    MAX_SUMMARY_CHARS,
    RING_CAPACITY,
    STATUS_ERROR,
    STATUS_OK,
    SubagentActivityLog,
)
from jenny.agent.subagent_records import (
    CANCEL_REASON_USER,
    SubagentRecord,
    SubagentSpec,
)
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider, ToolCallRequest

# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def _status(task_id: str = "t1") -> SubagentStatus:
    return SubagentStatus(
        task_id=task_id,
        label="lbl",
        task_description="do the thing",
        started_at=time.monotonic(),
    )


def _hook(
    task_id: str = "t1",
    *,
    log: SubagentActivityLog | None = None,
    status: SubagentStatus | None = None,
) -> tuple[_SubagentHook, SubagentActivityLog]:
    activity = log if log is not None else SubagentActivityLog()
    return _SubagentHook(task_id, status, activity=activity), activity


def _iteration_ctx(iteration: int = 1, tool_calls: list | None = None) -> AgentHookContext:
    return AgentHookContext(
        iteration=iteration,
        messages=[],
        tool_calls=tool_calls or [],
        tool_events=[],
        usage={},
    )


def _manager(tmp_path: Path, **kw) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    defaults = dict(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        stall_threshold_s=0.0,
    )
    defaults.update(kw)
    sm = SubagentManager(**defaults)
    sm.bus.publish_inbound = AsyncMock()
    return sm


async def _settle(sm: SubagentManager) -> None:
    tasks = [t for t in sm._running_tasks.values() if not t.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    # La done-callback (retain + digest + drop) gira al giro di loop successivo.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await sm._cancel_stall_watchdog()


def _record(task_id: str, *, session_key: str = "s1", **kw) -> SubagentRecord:
    stamp = time.time()
    return SubagentRecord(
        task_id=task_id,
        lineage_id=task_id,
        attempt=1,
        spec=SubagentSpec(task="do the thing", label="lbl", session_key=session_key),
        state="done",
        started_at=stamp - 1,
        ended_at=stamp,
        **kw,
    )


# ---------------------------------------------------------------------------
# granularita: una tool call, due eventi
# ---------------------------------------------------------------------------


class TestToolGranularity:
    async def test_start_is_emitted_per_call_before_execution(self):
        hook, log = _hook()
        await hook.before_execute_tools(_iteration_ctx(tool_calls=[
            ToolCallRequest(id="a", name="web_fetch", arguments={"url": "https://x.test/p"}),
            ToolCallRequest(id="b", name="read_file", arguments={"path": "/w/notes.md"}),
        ]))
        events = log.tail("t1")
        assert [e["kind"] for e in events] == [KIND_TOOL_START, KIND_TOOL_START]
        assert [e["name"] for e in events] == ["web_fetch", "read_file"]
        assert [e["call_id"] for e in events] == ["a", "b"]
        # La riga descrive l'AZIONE, non il tool: e cio che rende leggibile la
        # modale mentre il tool e ancora in volo.
        assert events[0]["summary"] == "opening x.test/p"
        assert events[1]["summary"] == "reading notes.md"

    async def test_end_carries_status_and_duration(self):
        hook, log = _hook()
        await hook.after_execute_tool(ToolResultHookContext(
            name="web_search",
            call_id="a",
            arguments={"query": "nostr relays"},
            result="1. one\n2. two\n3. three",
            duration_ms=1500,
        ))
        event = log.tail("t1")[0]
        assert event["kind"] == KIND_TOOL_END
        assert event["status"] == STATUS_OK
        assert event["duration_ms"] == 1500
        assert event["summary"] == "3 results"

    async def test_failed_tool_is_marked_error_not_dropped(self):
        hook, log = _hook()
        await hook.after_execute_tool(ToolResultHookContext(
            name="read_file",
            call_id="a",
            arguments={"path": "/w/gone.md"},
            result="Error: file not found: /w/gone.md",
        ))
        event = log.tail("t1")[0]
        assert event["status"] == STATUS_ERROR
        assert event["summary"] == "file not found"
        # Il messaggio grezzo non arriva alla UI: il path e contenuto.
        assert "/w/gone.md" not in event["summary"]

    async def test_a_broken_log_cannot_kill_the_hook(self):
        class _Hostile(SubagentActivityLog):
            def append(self, *a, **kw):
                raise RuntimeError("ring on fire")

        hook, _ = _hook(log=_Hostile())
        # Nessuna di queste deve sollevare: la telemetria e best-effort, il
        # lavoro del subagent no.
        await hook.before_execute_tools(_iteration_ctx(tool_calls=[
            ToolCallRequest(id="a", name="grep", arguments={"pattern": "x"}),
        ]))
        await hook.after_execute_tool(ToolResultHookContext(
            name="grep", call_id="a", arguments={}, result="ok",
        ))
        await hook.after_iteration(_iteration_ctx(iteration=1))
        await hook.emit_reasoning("thinking hard")
        await hook.emit_reasoning_end()

    async def test_hook_without_a_log_is_inert(self):
        hook = _SubagentHook("t1", _status())
        await hook.before_execute_tools(_iteration_ctx(tool_calls=[
            ToolCallRequest(id="a", name="grep", arguments={}),
        ]))
        await hook.after_iteration(_iteration_ctx(iteration=1))


# ---------------------------------------------------------------------------
# accoppiamento esatto: il caso che ha motivato ``call_id``
# ---------------------------------------------------------------------------


class TestConcurrentPairing:
    async def test_three_concurrent_web_fetch_pair_exactly(self):
        """Tre ``web_fetch`` in un batch, che finiscono in ordine inverso.

        Per nome del tool il FIFO accoppierebbe il primo start con il primo end,
        cioe l'URL sbagliato con la dimensione sbagliata: nel pannello si
        leggerebbe "opening a.test -> 2 KB" quando i 2 KB sono di c.test.
        """
        hook, log = _hook()
        calls = [
            ToolCallRequest(id="c1", name="web_fetch", arguments={"url": "https://a.test/1"}),
            ToolCallRequest(id="c2", name="web_fetch", arguments={"url": "https://b.test/2"}),
            ToolCallRequest(id="c3", name="web_fetch", arguments={"url": "https://c.test/3"}),
        ]
        await hook.before_execute_tools(_iteration_ctx(tool_calls=calls))
        # Ordine di completamento invertito rispetto a quello di partenza.
        for call_id, size in (("c3", 3), ("c2", 2), ("c1", 1)):
            await hook.after_execute_tool(ToolResultHookContext(
                name="web_fetch",
                call_id=call_id,
                arguments={},
                result="x" * (size * 1000),
                duration_ms=size * 100,
            ))

        digest = log.digest("t1")
        assert len(digest) == 3, "una chiamata, un evento nel digest"
        assert all(e["kind"] == DIGEST_KIND_TOOL for e in digest)
        pairs = {e["call_id"]: (e["summary"], e["duration_ms"]) for e in digest}
        assert "a.test/1" in pairs["c1"][0] and "1000 B" in pairs["c1"][0]
        assert "b.test/2" in pairs["c2"][0] and "2.0 KB" in pairs["c2"][0]
        assert "c.test/3" in pairs["c3"][0] and "2.9 KB" in pairs["c3"][0]
        assert pairs["c1"][1] == 100
        assert pairs["c3"][1] == 300

    async def test_a_call_still_in_flight_stays_incomplete(self):
        hook, log = _hook()
        await hook.before_execute_tools(_iteration_ctx(tool_calls=[
            ToolCallRequest(id="c1", name="grep", arguments={"pattern": "todo"}),
            ToolCallRequest(id="c2", name="grep", arguments={"pattern": "fixme"}),
        ]))
        await hook.after_execute_tool(ToolResultHookContext(
            name="grep", call_id="c2", arguments={}, result="a\nb",
        ))
        digest = log.digest("t1")
        statuses = {e["call_id"]: e["status"] for e in digest}
        assert statuses["c1"] == DIGEST_STATUS_INCOMPLETE
        assert statuses["c2"] == STATUS_OK

    async def test_missing_call_id_still_pairs_fifo(self):
        """Un produttore che non conosce il ``call_id`` non perde l'accoppiamento."""
        hook, log = _hook()
        await hook.before_execute_tools(_iteration_ctx(tool_calls=[
            ToolCallRequest(id=None, name="grep", arguments={"pattern": "todo"}),
        ]))
        await hook.after_execute_tool(ToolResultHookContext(
            name="grep", call_id="", arguments={}, result="a\nb",
        ))
        digest = log.digest("t1")
        assert len(digest) == 1
        assert digest[0]["status"] == STATUS_OK
        assert digest[0]["call_id"] is None


# ---------------------------------------------------------------------------
# segnale di pensiero
# ---------------------------------------------------------------------------


class TestThinkingSignal:
    async def test_a_reasoning_stream_is_coalesced(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        # 50 chunk dentro la stessa finestra di throttle: uno solo esce.
        for i in range(50):
            clock["now"] += _THINKING_THROTTLE_S / 100
            await hook.emit_reasoning(f"chunk {i}. ")
        assert len(log.tail("t1")) == 1

        # Oltre la finestra, il successivo passa.
        clock["now"] += _THINKING_THROTTLE_S
        await hook.emit_reasoning("and then this. ")
        assert len(log.tail("t1")) == 2

    async def test_a_reasoning_stream_cannot_flood_the_ring(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        # Mezz'ora di ragionamento a token: senza throttle sarebbero migliaia di
        # eventi e il ring conterrebbe SOLO ragionamento.
        for i in range(5000):
            clock["now"] += 0.01
            await hook.emit_reasoning(f"t{i} ")
        assert len(log.tail("t1")) < RING_CAPACITY

    async def test_elapsed_grows_within_one_segment(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        await hook.emit_reasoning("first thought. ")
        clock["now"] += 12.0
        await hook.emit_reasoning("second thought. ")
        events = log.tail("t1")
        assert events[0]["duration_ms"] == 0
        # 12s: e il numero che la UI mostra come "thinking - 12s", e viene da qui,
        # non da un orologio del client.
        assert events[1]["duration_ms"] == 12_000

    async def test_identical_text_still_emits_because_elapsed_changed(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        await hook.emit_reasoning("thinking about the schema")
        clock["now"] += 5.0
        # Chunk vuoto ripetuto: nessun testo nuovo. Ma il tempo passa, e il
        # segnale serve proprio a dire "sono ancora qui".
        await hook.emit_reasoning(" ")
        events = log.tail("t1")
        assert len(events) == 2
        assert events[1]["duration_ms"] == 5000

    async def test_end_flushes_the_coalesced_tail(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        await hook.emit_reasoning("first. ")
        clock["now"] += 0.01
        await hook.emit_reasoning("the conclusion is what matters. ")
        clock["now"] += 0.01
        await hook.emit_reasoning_end()
        events = log.tail("t1")
        assert len(events) == 2
        # La coda — cio a cui il modello e arrivato — non deve essere la parte che
        # il throttle butta via.
        assert "conclusion" in events[-1]["summary"]

    async def test_end_without_a_pending_update_emits_nothing_new(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        await hook.emit_reasoning("a single shot of reasoning")
        await hook.emit_reasoning_end()
        assert len(log.tail("t1")) == 1

    async def test_a_new_segment_restarts_the_elapsed(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        await hook.emit_reasoning("one")
        clock["now"] += 30.0
        await hook.emit_reasoning_end()
        clock["now"] += 60.0
        await hook.emit_reasoning("two")
        assert log.tail("t1")[-1]["duration_ms"] == 0

    async def test_summary_is_capped(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        await hook.emit_reasoning("word " * 4000)
        event = log.tail("t1")[0]
        assert len(event["summary"]) <= MAX_SUMMARY_CHARS
        # Il taglio e nostro, non del cap del log: nessun «…» aggiunto sopra.
        assert not event["summary"].endswith(("...", "…"))

    async def test_thinking_counts_as_progress(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        status = _status()
        status.last_progress_at = 1.0
        status.state = "stalled"
        hook, _ = _hook(status=status)
        await hook.emit_reasoning("still working on it")
        # Un modello che ragiona da tre minuti non e un subagent bloccato.
        assert status.last_progress_at == 1000.0
        assert status.state == "running"

    async def test_answer_text_is_a_separate_label(self, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "jenny.agent.subagent.time.monotonic", lambda: clock["now"]
        )
        hook, log = _hook()
        hook.note_output("Here is what I found: the relay list is stale.")
        event = log.tail("t1")[0]
        assert event["kind"] == KIND_THINKING
        assert event["summary"].startswith("writing: ")


class TestThinkingExcerpt:
    def test_short_text_is_returned_whole(self):
        assert _thinking_excerpt("  a short   thought ") == "a short thought"

    def test_long_text_resumes_at_a_sentence_boundary(self):
        text = (
            "First I considered the caching layer, which turned out to be irrelevant. "
            "Then I looked at the retry policy and found the backoff was multiplied twice. "
            "So the fix belongs in the provider, not in the loop."
        )
        excerpt = _thinking_excerpt(text)
        assert len(excerpt) <= _THINKING_EXCERPT_CHARS
        # Inizia dove inizia un pensiero, non a meta parola, e prende la frase
        # piu lunga che ci sta: il lettore vuole contesto, non il minimo.
        assert excerpt.startswith("Then I looked at the retry policy")
        assert excerpt.endswith("not in the loop.")

    def test_a_wall_of_text_falls_back_to_a_word_boundary(self):
        text = "alpha " * 200
        excerpt = _thinking_excerpt(text)
        assert len(excerpt) <= _THINKING_EXCERPT_CHARS
        assert excerpt.startswith("alpha")

    def test_a_single_unbroken_token_still_yields_something(self):
        excerpt = _thinking_excerpt("x" * 900)
        assert 0 < len(excerpt) <= _THINKING_EXCERPT_CHARS

    def test_newlines_and_control_characters_do_not_survive(self):
        excerpt = _thinking_excerpt("line one\n\nline\ttwo")
        assert excerpt == "line one line two"


# ---------------------------------------------------------------------------
# gli altri produttori
# ---------------------------------------------------------------------------


class TestOtherProducers:
    async def test_iteration_event_per_iteration(self):
        hook, log = _hook()
        await hook.after_iteration(_iteration_ctx(iteration=3))
        event = log.tail("t1")[0]
        assert event["kind"] == KIND_ITERATION
        assert event["summary"] == "iteration 3"

    def test_phase_is_translated_for_a_reader(self):
        hook, log = _hook()
        hook.note_phase("awaiting_tools")
        assert log.tail("t1")[0]["kind"] == KIND_PHASE
        assert log.tail("t1")[0]["summary"] == _PHASE_LABELS["awaiting_tools"]

    def test_unknown_phase_degrades_instead_of_disappearing(self):
        hook, log = _hook()
        hook.note_phase("some_new_phase")
        assert log.tail("t1")[0]["summary"] == "some new phase"

    def test_empty_phase_is_ignored(self):
        hook, log = _hook()
        hook.note_phase("")
        hook.note_phase(None)
        assert log.tail("t1") == []

    def test_message_in_reports_the_count_and_not_the_text(self):
        hook, log = _hook()
        hook.note_message_in(2)
        event = log.tail("t1")[0]
        assert event["kind"] == KIND_MESSAGE_IN
        assert event["summary"] == "received 2 messages from the orchestrator"

    def test_terminal_result_carries_a_measure(self):
        hook, log = _hook()
        hook.note_result(1234)
        event = log.tail("t1")[0]
        assert event["kind"] == KIND_RESULT
        assert event["status"] == STATUS_OK
        assert "1234 characters" in event["summary"]

    def test_terminal_error_is_our_own_phrase(self):
        hook, log = _hook()
        hook.note_error("crashed (ValueError)")
        event = log.tail("t1")[0]
        assert event["kind"] == KIND_ERROR
        assert event["status"] == STATUS_ERROR
        assert event["summary"] == "crashed (ValueError)"


# ---------------------------------------------------------------------------
# il subagent, end-to-end
# ---------------------------------------------------------------------------


def _runner_that(sm: SubagentManager, *, result: AgentRunResult) -> list[Any]:
    """Sostituisce il runner e cattura le spec con cui e stato invocato."""
    seen: list[Any] = []

    async def _run(spec):
        seen.append(spec)
        return result

    sm.runner.run = _run
    return seen


class TestSubagentWiring:
    async def test_the_run_spec_carries_a_progress_callback(self, tmp_path: Path):
        sm = _manager(tmp_path)
        seen = _runner_that(sm, result=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("research something", session_key="s1")
        await _settle(sm)
        assert len(seen) == 1
        callback = seen[0].progress_callback
        assert callback is not None
        # Firma deliberatamente stretta: dichiarare questi parametri accenderebbe
        # i tracker di file-edit del runner, che sono UI della chat principale.
        import inspect

        params = inspect.signature(callback).parameters
        assert "file_edit_events" not in params
        assert "tool_events" not in params

    async def test_a_completed_subagent_writes_its_digest_once(self, tmp_path: Path):
        sm = _manager(tmp_path)
        _runner_that(sm, result=AgentRunResult(
            final_content="the answer", messages=[], stop_reason="completed",
        ))
        await sm.spawn("research something", session_key="s1")
        await _settle(sm)

        records = sm.list_records("s1")
        assert len(records) == 1
        record = records[0]
        assert record.has_activity_digest

        path = sm.digests.path_for(record.task_id)
        assert path is not None and path.is_file()
        payload = json.loads(path.read_text(encoding="utf-8"))
        # I contatori del record devono descrivere il file che c'e davvero:
        # e con quelli che la UI decide se offrire il blocco, senza aprirlo.
        assert record.activity_events == len(payload["events"])
        assert record.activity_bytes == len(path.read_text(encoding="utf-8").encode())

        kinds = [e["kind"] for e in payload["events"]]
        assert KIND_PHASE in kinds
        assert KIND_RESULT in kinds

    async def test_the_ring_is_dropped_after_the_digest_is_written(self, tmp_path: Path):
        sm = _manager(tmp_path)
        _runner_that(sm, result=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        task_id = None
        await sm.spawn("research something", session_key="s1")
        task_id = next(iter(sm._task_statuses))
        await _settle(sm)
        # Il durevole e il digest: il ring vivo non deve sopravvivere al task.
        assert sm.activity.task_ids() == []
        assert sm.activity.tail(task_id) == []

    async def test_a_failed_run_still_records_how_it_ended(self, tmp_path: Path):
        sm = _manager(tmp_path)
        _runner_that(sm, result=AgentRunResult(
            final_content=None, messages=[], stop_reason="tool_error",
            tool_events=[{"name": "web_fetch", "status": "error", "detail": "boom"}],
        ))
        await sm.spawn("research something", session_key="s1")
        await _settle(sm)
        record = sm.list_records("s1")[0]
        path = sm.digests.path_for(record.task_id)
        assert path is not None
        events = json.loads(path.read_text(encoding="utf-8"))["events"]
        assert any(e["kind"] == KIND_ERROR for e in events)

    async def test_a_crash_records_only_the_exception_class(self, tmp_path: Path):
        sm = _manager(tmp_path)

        async def _boom(spec):
            raise ValueError("secret /private/path detail")

        sm.runner.run = _boom
        await sm.spawn("research something", session_key="s1")
        await _settle(sm)
        record = sm.list_records("s1")[0]
        path = sm.digests.path_for(record.task_id)
        assert path is not None
        raw = path.read_text(encoding="utf-8")
        assert "crashed (ValueError)" in raw
        assert "/private/path" not in raw

    async def test_a_cancelled_subagent_says_who_stopped_it(self, tmp_path: Path):
        sm = _manager(tmp_path)
        block = asyncio.Event()

        async def _run(spec):
            await block.wait()
            return AgentRunResult(final_content="x", messages=[], stop_reason="completed")

        sm.runner.run = _run
        await sm.spawn("research something", session_key="s1")
        task_id = next(iter(sm._task_statuses))
        await asyncio.sleep(0)
        await sm.cancel_task(task_id, grace_s=0.5)
        await _settle(sm)
        record = sm.list_records("s1")[0]
        path = sm.digests.path_for(record.task_id)
        assert path is not None
        # Una cancellazione non passa dai rami di ``_run_subagent``: senza
        # l'evento scritto dove la provenienza viene decisa, il digest finirebbe
        # senza dire come il lavoro e finito.
        assert f"cancelled ({CANCEL_REASON_USER})" in path.read_text(encoding="utf-8")

    async def test_a_publishing_failure_does_not_kill_the_subagent(self, tmp_path: Path):
        sm = _manager(tmp_path)

        def _explode(_msg):
            raise RuntimeError("outbound queue on fire")

        sm.bus.try_publish_outbound = _explode
        _runner_that(sm, result=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("research something", session_key="s1")
        await _settle(sm)
        # Il lavoro e finito e il record c'e: la telemetria e best-effort.
        record = sm.list_records("s1")[0]
        assert record.state == "done"
        assert record.has_activity_digest

    async def test_a_broken_digest_store_cannot_lose_the_record(self, tmp_path: Path):
        sm = _manager(tmp_path)

        def _explode(*a, **kw):
            raise OSError("read-only filesystem")

        sm.digests.write = _explode  # type: ignore[method-assign]
        _runner_that(sm, result=AgentRunResult(
            final_content="done", messages=[], stop_reason="completed",
        ))
        await sm.spawn("research something", session_key="s1")
        await _settle(sm)
        record = sm.list_records("s1")[0]
        assert record.state == "done"
        assert not record.has_activity_digest
        assert sm.activity.task_ids() == []


class TestTransportSurface:
    def test_the_log_and_the_store_are_public_attributes(self, tmp_path: Path):
        """Contratto verso il transport: questi due nomi, non altri."""
        sm = _manager(tmp_path)
        assert isinstance(sm.activity, SubagentActivityLog)
        assert sm.digests.root == tmp_path / "subagents" / "activity"

    def test_the_record_store_can_delete_digests(self, tmp_path: Path):
        sm = _manager(tmp_path)
        assert sm._records._digests is sm.digests


class TestOrphanSweep:
    def test_a_digest_without_a_record_is_swept_at_startup(self, tmp_path: Path):
        seed = _manager(tmp_path)
        seed._records.append(_record("kept", activity_events=2, activity_bytes=99))
        seed.digests.write("kept", [
            {"kind": KIND_PHASE, "summary": "starting up"},
        ])
        # Il residuo di un processo ucciso *tra* la scrittura del digest e quella
        # del record: nessuna potatura lo vedrebbe mai, perche la potatura guarda
        # i record che escono.
        seed.digests.write("orphan", [
            {"kind": KIND_PHASE, "summary": "starting up"},
        ])
        assert seed.digests.path_for("orphan").is_file()

        reborn = _manager(tmp_path)
        assert reborn.digests.path_for("orphan").is_file() is False
        assert reborn.digests.path_for("kept").is_file() is True

    def test_the_sweep_never_blocks_a_boot(self, tmp_path: Path):
        sm = _manager(tmp_path)

        def _explode(*a, **kw):
            raise OSError("no")

        sm.digests.keep_only = _explode  # type: ignore[method-assign]
        assert sm.sweep_orphan_digests() == 0

    def test_an_unusable_workspace_is_not_a_boot_failure(self):
        sm = _manager(None)  # type: ignore[arg-type]
        assert sm.sweep_orphan_digests() == 0


# ---------------------------------------------------------------------------
# l'agente principale non cambia
# ---------------------------------------------------------------------------


class TestMainAgentUnaffected:
    async def test_the_progress_hook_ignores_the_new_tool_hook(self):
        """L'hook e condiviso: il default e no-op e ``AgentProgressHook`` non lo usa."""
        from jenny.agent.progress_hook import AgentProgressHook

        emitted: list[tuple] = []

        async def _on_progress(content, *, tool_hint=False, tool_events=None,
                               file_edit_events=None, reasoning=False,
                               reasoning_end=False):
            emitted.append((content, tool_hint, reasoning))

        hook = AgentProgressHook(on_progress=_on_progress)
        await hook.after_execute_tool(ToolResultHookContext(
            name="read_file", call_id="a", arguments={}, result="x",
        ))
        assert emitted == []

    async def test_a_composite_isolates_a_raising_consumer(self):
        from jenny.agent.hook import CompositeHook

        class _Boom(AgentHookSpy):
            async def after_execute_tool(self, context):
                raise RuntimeError("boom")

        good = AgentHookSpy()
        composite = CompositeHook([_Boom(), good])
        await composite.after_execute_tool(ToolResultHookContext(
            name="read_file", call_id="a", arguments={},
        ))
        # Il secondo hook viene chiamato comunque: un consumatore rotto non deve
        # poter zittire gli altri ne uccidere il turno.
        assert good.seen == 1


class AgentHookSpy:
    """Hook minimale che conta le chiamate (non deriva da AgentHook: serve solo
    la firma che ``CompositeHook`` invoca)."""

    _reraise = False

    def __init__(self) -> None:
        self.seen = 0

    async def after_execute_tool(self, context) -> None:
        self.seen += 1
