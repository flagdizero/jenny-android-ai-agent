"""Il ring dell'attivita non perde i tool per far posto al ragionamento.

Prova del 24/10/2026: un campione di ragionamento ogni 0,4 s riempiva il ring da
200 eventi in ~80 s, e un aiutante lungo arrivava al digest senza i suoi primi
tool — proprio quel che il blocco «cosa ha fatto davvero» deve raccontare. Ora
un campione superato dal successivo dello stesso segmento, e un ``iteration``
superato dal successivo, escono per primi; poi il rumore; i tool per ultimi.
"""

from __future__ import annotations

from jenny.agent.subagent_activity import (
    DIGEST_KIND_TOOL,
    KIND_ITERATION,
    KIND_PHASE,
    KIND_THINKING,
    KIND_TOOL_END,
    KIND_TOOL_START,
    RING_CAPACITY,
    SubagentActivityLog,
)

TASK = "t1"
ITERATIONS = 30
SAMPLES = 34  # 30 x 34 = 1020 campioni di ragionamento
STEP_MS = 400


def _one_iteration(log: SubagentActivityLog, i: int) -> None:
    """Quel che il ``_SubagentHook`` produce in un giro: pensiero, un tool, fasi."""
    for j in range(SAMPLES):
        log.append(TASK, KIND_THINKING, summary=f"thinking: step {i}.{j}",
                   duration_ms=j * STEP_MS)
    log.append(TASK, KIND_PHASE, summary="running tools")
    log.append(TASK, KIND_TOOL_START, summary=f"read page {i}", name="read_file",
               call_id=f"call-{i}")
    log.append(TASK, KIND_TOOL_END, summary="120 lines", name="read_file",
               call_id=f"call-{i}", status="ok", duration_ms=50)
    log.append(TASK, KIND_PHASE, summary="tools finished")
    log.append(TASK, KIND_ITERATION, summary=f"iteration {i}")


def test_a_thousand_thinking_samples_do_not_push_out_thirty_tools() -> None:
    log = SubagentActivityLog()
    for i in range(ITERATIONS):
        _one_iteration(log, i)

    events = log.tail(TASK)
    assert len(events) <= RING_CAPACITY
    starts = [e for e in events if e["kind"] == KIND_TOOL_START]
    ends = [e for e in events if e["kind"] == KIND_TOOL_END]
    assert len(starts) == ITERATIONS and len(ends) == ITERATIONS

    digest = log.digest(TASK)
    assert sum(1 for e in digest if e["kind"] == DIGEST_KIND_TOOL) == ITERATIONS
    assert all(e["status"] == "ok" for e in digest if e["kind"] == DIGEST_KIND_TOOL)


def test_the_digest_still_counts_segments_and_their_longest_sample() -> None:
    log = SubagentActivityLog()
    for i in range(ITERATIONS):
        _one_iteration(log, i)

    thinking = [e for e in log.digest(TASK) if e["kind"] == KIND_THINKING]
    assert len(thinking) == 1
    # Somma dei massimi per segmento: 30 segmenti da (34-1) x 400 ms.
    assert thinking[0]["duration_ms"] == ITERATIONS * (SAMPLES - 1) * STEP_MS
    assert thinking[0]["summary"].startswith(f"thinking: {ITERATIONS} steps")


def test_a_segment_keeps_its_start_and_its_latest_sample_when_full() -> None:
    log = SubagentActivityLog(capacity=6)
    log.append(TASK, KIND_TOOL_START, summary="a", name="grep", call_id="c1")
    for j in range(20):
        log.append(TASK, KIND_THINKING, summary=f"thinking: {j}", duration_ms=j * STEP_MS)
    kept = [e["duration_ms"] for e in log.tail(TASK) if e["kind"] == KIND_THINKING]
    assert kept[0] == 0, "the segment start survives"
    assert kept[-1] == 19 * STEP_MS, "the latest sample survives"
    assert log.tail(TASK)[0]["kind"] == KIND_TOOL_START


def test_writing_and_thinking_are_separate_segments() -> None:
    log = SubagentActivityLog(capacity=4)
    for j in range(5):
        log.append(TASK, KIND_THINKING, summary=f"thinking: {j}", duration_ms=j * STEP_MS)
    for j in range(5):
        log.append(TASK, KIND_THINKING, summary=f"writing: {j}", duration_ms=j * STEP_MS)
    thinking = [e for e in log.digest(TASK) if e["kind"] == KIND_THINKING][0]
    # Due segmenti: 4 x 400 + 4 x 400.
    assert thinking["duration_ms"] == 2 * 4 * STEP_MS
    assert thinking["summary"].startswith("thinking: 2 steps")


def test_a_reader_that_keeps_up_sees_every_update_and_no_gap() -> None:
    """Chi legge in coda col proprio cursore vede ogni campione, senza buchi:
    il campione superato esce dal ring solo quando serve spazio, in testa."""
    log = SubagentActivityLog(capacity=10)
    cursor = 0
    seen = 0
    for j in range(40):
        log.append(TASK, KIND_THINKING, summary=f"thinking: {j}", duration_ms=j * STEP_MS)
        window = log.tail_window(TASK, since_seq=cursor)
        assert window.gap is False
        seen += len(window.events)
        cursor = window.last_seq or cursor
    assert seen == 40
