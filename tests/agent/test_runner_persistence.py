"""Tests for tool result persistence: large results, pruning, temp files, cleanup."""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

from support.runner import empty_tools, make_spec

from jenny.providers.base import LLMResponse, ToolCallRequest


async def test_runner_persists_large_tool_results_for_follow_up_calls(tmp_path):
    from jenny.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_second_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="working",
                tool_calls=[ToolCallRequest(id="call_big", name="list_dir", arguments={"path": "."})],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        captured_second_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="x" * 20_000)

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "do task"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        workspace=tmp_path,
        session_key="test:runner",
        max_tool_result_chars=2048,
    ))

    assert result.final_content == "done"
    tool_message = next(msg for msg in captured_second_call if msg.get("role") == "tool")
    assert "[tool output persisted]" in tool_message["content"]
    assert "tool-results" in tool_message["content"]
    assert (tmp_path / ".jenny" / "tool-results" / "test_runner" / "call_big.txt").exists()


def test_persist_tool_result_prunes_old_session_buckets(tmp_path):
    from jenny.utils.helpers import maybe_persist_tool_result

    root = tmp_path / ".jenny" / "tool-results"
    old_bucket = root / "old_session"
    recent_bucket = root / "recent_session"
    old_bucket.mkdir(parents=True)
    recent_bucket.mkdir(parents=True)
    (old_bucket / "old.txt").write_text("old", encoding="utf-8")
    (recent_bucket / "recent.txt").write_text("recent", encoding="utf-8")

    stale = time.time() - (8 * 24 * 60 * 60)
    os.utime(old_bucket, (stale, stale))
    os.utime(old_bucket / "old.txt", (stale, stale))

    persisted = maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert "[tool output persisted]" in persisted
    assert not old_bucket.exists()
    assert recent_bucket.exists()
    assert (root / "current_session" / "call_big.txt").exists()


def test_persist_tool_result_leaves_no_temp_files(tmp_path):
    from jenny.utils.helpers import maybe_persist_tool_result

    root = tmp_path / ".jenny" / "tool-results"
    maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert (root / "current_session" / "call_big.txt").exists()
    assert list((root / "current_session").glob("*.tmp")) == []


def test_persist_tool_result_logs_cleanup_failures(monkeypatch, tmp_path):
    from jenny.utils.helpers import maybe_persist_tool_result

    warnings: list[str] = []

    monkeypatch.setattr(
        "jenny.utils.helpers._cleanup_tool_result_buckets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")),
    )
    monkeypatch.setattr(
        "jenny.utils.helpers.logger.exception",
        lambda message, *args: warnings.append(message.format(*args)),
    )

    persisted = maybe_persist_tool_result(
        tmp_path,
        "current:session",
        "call_big",
        "x" * 5000,
        max_chars=64,
    )

    assert "[tool output persisted]" in persisted
    assert warnings and "Failed to clean stale tool result buckets" in warnings[0]


async def test_read_file_result_is_not_offloaded(tmp_path):
    """read_file must not trigger generic offloading (prevents persist->read->persist loops)."""
    from jenny.agent.runner import AgentRunner, AgentRunSpec

    provider = MagicMock()
    captured_second_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="reading",
                tool_calls=[ToolCallRequest(id="call_rf", name="read_file", arguments={"path": "big.txt"})],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        captured_second_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="x" * 20_000)

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "read big file"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        workspace=tmp_path,
        session_key="test:runner",
        max_tool_result_chars=2048,
    ))

    assert result.final_content == "done"
    tool_message = next(msg for msg in captured_second_call if msg.get("role") == "tool")
    # read_file result must NOT be offloaded to a file
    assert "[tool output persisted]" not in tool_message["content"]
    # read_file manages its own size; generic truncation must NOT apply
    assert len(tool_message["content"]) == 20_000
    # no file should have been written for this read_file call
    offload_dir = tmp_path / ".jenny" / "tool-results"
    assert not any(offload_dir.rglob("call_rf.txt")) if offload_dir.exists() else True


async def test_runner_keeps_going_when_tool_result_persistence_fails():
    from jenny.agent.runner import AgentRunner

    provider = MagicMock()
    captured_second_call: list[dict] = []
    call_count = {"n": 0}

    async def chat_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="working",
                tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        captured_second_call[:] = messages
        return LLMResponse(content="done", tool_calls=[], usage={})

    provider.chat_with_retry = chat_with_retry
    tools = empty_tools()

    runner = AgentRunner(provider)
    with patch("jenny.agent.runner.maybe_persist_tool_result", side_effect=RuntimeError("disk full")):
        result = await runner.run(make_spec(
            initial_messages=[{"role": "user", "content": "do task"}],
            tools=tools,
            max_iterations=2,
        ))

    assert result.final_content == "done"
    tool_message = next(msg for msg in captured_second_call if msg.get("role") == "tool")
    assert tool_message["content"] == "tool result"


# ---------------------------------------------------------------------------
# Riferimento all'output spillato: righe, non solo caratteri
# ---------------------------------------------------------------------------


def test_persisted_reference_states_the_line_count_and_where_to_resume(tmp_path):
    """Il riferimento non deve costringere il modello a inventarsi un ``offset``.

    ``read_file`` pagina per riga e rifiuta un offset oltre la fine con un errore
    tool; un riferimento che parla solo di caratteri rende quell'errore
    inevitabile per chiunque voglia "il resto". Il conteggio delle righe e la riga
    da cui riprendere sono esattamente cio che serve a non tirare a indovinare.
    """
    from jenny.utils.helpers import maybe_persist_tool_result

    payload = "\n".join(f"line {i}" for i in range(1, 501))
    persisted = maybe_persist_tool_result(
        tmp_path, "current:session", "call_big", payload, max_chars=64,
    )

    assert "[tool output persisted]" in persisted
    assert "500 lines" in persisted
    # La preview e tagliata a 1200 caratteri, quindi l'offset di ripresa cade
    # dentro il file e non oltre la fine.
    stored = (tmp_path / ".jenny" / "tool-results" / "current_session" / "call_big.txt")
    total = len(stored.read_text(encoding="utf-8").splitlines())
    assert total == 500
    offset = int(persisted.split("offset=")[1].split(")")[0])
    assert 1 <= offset <= total
    # La riga indicata e la prima non mostrata per intero: rileggerla da lì non
    # salta niente.
    assert f"line {offset}" in payload.splitlines()[offset - 1]


def test_persisted_reference_line_count_describes_the_written_file(tmp_path):
    """Per i blocchi testuali su disco finisce il JSON indentato, non la preview.

    Il conteggio deve descrivere il file che il modello aprirebbe; e per lo stesso
    motivo l'offset di ripresa, che si riferisce alla preview, qui non viene dato.
    """
    from jenny.utils.helpers import maybe_persist_tool_result

    blocks = [{"type": "text", "text": "x" * 4000}]
    persisted = maybe_persist_tool_result(
        tmp_path, "current:session", "call_blocks", blocks, max_chars=64,
    )

    stored = tmp_path / ".jenny" / "tool-results" / "current_session" / "call_blocks.json"
    expected = len(stored.read_text(encoding="utf-8").splitlines())
    assert f"{expected} lines" in persisted
    assert "offset=" not in persisted


def test_persisted_reference_without_a_cut_preview_still_reports_lines(tmp_path):
    """Anche senza taglio il conteggio c'e: e il campo, non l'avviso, a informare."""
    from jenny.utils.helpers import maybe_persist_tool_result

    payload = "\n".join(["short"] * 20)
    persisted = maybe_persist_tool_result(
        tmp_path, "current:session", "call_small", payload, max_chars=32,
    )

    assert "20 lines" in persisted
    assert "Preview cut" not in persisted
