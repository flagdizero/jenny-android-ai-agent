import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.context import ContextBuilder
from jenny.agent.loop import AgentLoop
from jenny.bus.events import InboundMessage
from jenny.bus.queue import MessageBus
from jenny.cron.session_turns import CRON_HISTORY_META, CRON_TRIGGER_META
from jenny.providers.base import LLMResponse
from jenny.session.goal_state import GOAL_STATE_KEY
from jenny.session.history_meta import (
    INJECTED_EVENT_META,
    SUBAGENT_RESULT_EVENT,
    is_synthetic_history_row,
)
from jenny.session.keys import UNIFIED_SESSION_KEY
from jenny.session.manager import Session
from jenny.session.turn_continuation import (
    INTERNAL_CONTINUATION_META,
    INTERNAL_CONTINUATION_RUN_STARTED_AT_META,
)
from jenny.session.webui_turns import WebuiTurnCoordinator

# La chiave esplicita del turno, diversa da quella della chat: serve a un solo
# test, che controlla che il goal di una chat non finisca nel contesto di
# un'altra sessione. Era ``"system"`` — un prefisso che nessun vocabolario
# registra, quindi da T4.10 ``session_kind`` la classifica ``internal`` e
# ``resolve_turn_visibility`` rende il turno SILENT su un canale utente: il
# ``process_direct`` non tornava piu' niente. Il soggetto del test e' l'isolamento
# fra due sessioni, non la visibilita'.
_OTHER_KEY = "websocket:system"


def _mk_loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    from jenny.config.schema import AgentDefaults

    loop.max_tool_result_chars = AgentDefaults().max_tool_result_chars
    return loop


def _make_full_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="Test title"))
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")
    WebuiTurnCoordinator(
        bus=loop.bus,
        sessions=loop.sessions,
        schedule_background=lambda coro: loop._schedule_background(coro),
    ).subscribe(loop.runtime_events)
    return loop


async def test_agent_loop_llm_runtime_reflects_current_provider_and_model(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    runtime = await loop.llm_runtime()

    assert runtime.provider is loop.provider
    assert runtime.model == "test-model"

    next_provider = MagicMock()
    loop.provider = next_provider
    loop.model = "next-model"
    runtime = await loop.llm_runtime()

    assert runtime.provider is next_provider
    assert runtime.model == "next-model"


def test_persist_cron_turn_uses_distinct_history_marker(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:auto")
    prompt_ref = {"id": "cron.agent_turn.reminder", "version": 1, "sha256": "abc"}

    persisted = loop._persist_user_message_early(
        InboundMessage(
            channel="websocket",
            sender_id="cron",
            chat_id="auto",
            content="Cron job: internal prompt",
            metadata={
                CRON_TRIGGER_META: {
                    "job_id": "job-1",
                    "job_name": "Daily check",
                    "run_id": "job-1:1",
                    "prompt_ref": prompt_ref,
                    "persist_content": "Scheduled cron job triggered: Daily check",
                }
            },
        ),
        session,
    )

    assert persisted is True
    message = session.messages[-1]
    assert message["content"] == "Scheduled cron job triggered: Daily check"
    assert message[CRON_HISTORY_META] is True
    assert CRON_TRIGGER_META not in message
    assert message["cron_job_id"] == "job-1"
    assert message["cron_job_name"] == "Daily check"
    assert message["cron_run_id"] == "job-1:1"
    assert message["cron_prompt_ref"] == prompt_ref


@pytest.mark.asyncio
async def test_injected_subagent_result_is_marked_in_history(tmp_path: Path) -> None:
    """L'annuncio di rientro di un subagent non si confonde con l'utente.

    Il gemello di ``test_persist_cron_turn_uses_distinct_history_marker`` per
    l'altra riga sintetica. ``AgentLoop._drain_pending`` normalizza qualunque
    messaggio in coda a ``{"role": "user", ...}`` — è la forma che il modello
    deve vedere — e il turno si persiste con quella forma. Senza riportare la
    metadata, il rientro di un subagent finiva in storia indistinguibile da una
    frase digitata: bolla in chat, titolo della conversazione, riarmo
    dell'heartbeat (v. ``jenny.session.history_meta``).
    """
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.provider.chat_with_retry = AsyncMock(
        side_effect=[
            LLMResponse(content="delego al subagent", finish_reason="stop"),
            LLMResponse(content="backup ok", finish_reason="stop"),
        ]
    )

    pending: asyncio.Queue = asyncio.Queue()
    pending.put_nowait(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="websocket:c-announce",
            content="[Subagent 'backup' completed successfully]\n\nTask: leggi l'hub…",
            metadata={
                INJECTED_EVENT_META: SUBAGENT_RESULT_EVENT,
                "subagent_task_id": "ff0941f9",
            },
        )
    )

    await loop._process_message(
        InboundMessage(
            channel="websocket",
            sender_id="u1",
            chat_id="c-announce",
            content="com'è andato il backup?",
        ),
        pending_queue=pending,
    )

    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    announce = [
        m for m in session.messages
        if isinstance(m.get("content"), str) and m["content"].startswith("[Subagent ")
    ]
    assert len(announce) == 1, session.messages
    assert announce[0]["role"] == "user"
    assert announce[0][INJECTED_EVENT_META] == SUBAGENT_RESULT_EVENT
    assert announce[0]["subagent_task_id"] == "ff0941f9"
    assert is_synthetic_history_row(announce[0]) is True

    # Il messaggio vero dell'utente resta quello che è.
    typed = [m for m in session.messages if m.get("content") == "com'è andato il backup?"]
    assert len(typed) == 1
    assert is_synthetic_history_row(typed[0]) is False


def test_save_turn_skips_multimodal_user_when_only_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:runtime-only")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._save_turn(
        session,
        [{"role": "user", "content": [{"type": "text", "text": runtime}]}],
        skip=0,
    )
    assert session.messages == []


def test_save_turn_keeps_image_placeholder_with_path_after_runtime_strip() -> None:
    loop = _mk_loop()
    session = Session(key="test:image")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._save_turn(
        session,
        [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}, "_meta": {"path": "/media/websocket/photo.jpg"}},
                {"type": "text", "text": runtime},
            ],
        }],
        skip=0,
    )
    assert session.messages[0]["content"] == [{"type": "text", "text": "[image: /media/websocket/photo.jpg]"}]


def test_save_turn_keeps_image_placeholder_without_meta() -> None:
    loop = _mk_loop()
    session = Session(key="test:image-no-meta")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._save_turn(
        session,
        [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                {"type": "text", "text": runtime},
            ],
        }],
        skip=0,
    )
    assert session.messages[0]["content"] == [{"type": "text", "text": "[image]"}]


def test_save_turn_strips_runtime_context_suffix_from_string() -> None:
    loop = _mk_loop()
    session = Session(key="test:suffix-strip")
    runtime = (
        ContextBuilder._RUNTIME_CONTEXT_TAG
        + "\nCurrent Time: now\n"
        + ContextBuilder._RUNTIME_CONTEXT_END
    )

    loop._save_turn(
        session,
        [{"role": "user", "content": f"hello world\n\n{runtime}"}],
        skip=0,
    )
    assert session.messages[0]["content"] == "hello world"


def test_save_turn_skips_string_user_when_only_runtime_context_suffix() -> None:
    loop = _mk_loop()
    session = Session(key="test:suffix-only")
    runtime = (
        ContextBuilder._RUNTIME_CONTEXT_TAG
        + "\nCurrent Time: now\n"
        + ContextBuilder._RUNTIME_CONTEXT_END
    )

    loop._save_turn(
        session,
        [{"role": "user", "content": runtime}],
        skip=0,
    )
    assert session.messages == []


def test_save_turn_keeps_tool_results_under_16k() -> None:
    loop = _mk_loop()
    session = Session(key="test:tool-result")
    content = "x" * 12_000

    loop._save_turn(
        session,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": content},
        ],
        skip=0,
    )

    assert session.messages[1]["content"] == content


def test_save_turn_stamps_latency_on_last_assistant() -> None:
    loop = _mk_loop()
    session = Session(key="test:latency")

    loop._save_turn(
        session,
        [
            {"role": "assistant", "content": "hello", "tool_calls": [{"id": "c1"}]},
            {"role": "assistant", "content": "final answer"},
        ],
        skip=0,
        turn_latency_ms=12345,
    )

    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "final answer"
    assert session.messages[-1]["latency_ms"] == 12345


def test_restore_runtime_checkpoint_rehydrates_completed_and_pending_tools() -> None:
    loop = _mk_loop()
    session = Session(
        key="test:checkpoint",
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "python_exec", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "python_exec", "arguments": "{}"},
                    }
                ],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is None
    assert session.messages[0]["role"] == "assistant"
    assert session.messages[1]["tool_call_id"] == "call_done"
    assert session.messages[2]["tool_call_id"] == "call_pending"
    assert "interrupted before this tool finished" in session.messages[2]["content"].lower()


def test_restore_runtime_checkpoint_dedupes_overlapping_tail() -> None:
    loop = _mk_loop()
    session = Session(
        key="test:checkpoint-overlap",
        messages=[
            {
                "role": "assistant",
                "content": "working",
                "tool_calls": [
                    {
                        "id": "call_done",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "python_exec", "arguments": "{}"},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_done",
                "name": "read_file",
                "content": "ok",
            },
        ],
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "python_exec", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "python_exec", "arguments": "{}"},
                    }
                ],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is None
    assert len(session.messages) == 3
    assert session.messages[0]["role"] == "assistant"
    assert session.messages[1]["tool_call_id"] == "call_done"
    assert session.messages[2]["tool_call_id"] == "call_pending"


@pytest.mark.asyncio
async def test_process_message_persists_user_message_before_turn_completes(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c1", content="persist me")
    with pytest.raises(RuntimeError, match="boom"):
        await loop._process_message(msg)

    loop.sessions.invalidate(UNIFIED_SESSION_KEY)
    persisted = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [m["role"] for m in persisted.messages] == ["user"]
    assert persisted.messages[0]["content"] == "persist me"
    assert persisted.metadata.get(AgentLoop._PENDING_USER_TURN_KEY) is True
    assert persisted.updated_at >= persisted.created_at


# 1x1 PNG used by the media-persistence tests. ``extract_documents`` runs
# at the top of ``_process_message`` and filters ``msg.media`` down to
# paths that magic-byte-sniff as images, so the test fixture needs real
# bytes on disk (not just placeholder paths).
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x00\x00\x02\x00\x01"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_process_message_persists_media_paths_on_user_turn(tmp_path: Path) -> None:
    """User turns that attach images must record the media paths alongside
    the text so the webui can rehydrate previews on session replay.

    This is the producer half of the signed-media-URL round-trip: paths are
    stored here, then :meth:`WebSocketChannel._augment_media_urls` maps them
    onto signed URLs on the way out.
    """
    img_a = tmp_path / "uuid-1.png"
    img_a.write_bytes(_PNG_1X1)
    img_b = tmp_path / "uuid-2.png"
    img_b.write_bytes(_PNG_1X1)

    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("interrupt"))  # type: ignore[method-assign]

    msg = InboundMessage(
        channel="websocket",
        sender_id="u1",
        chat_id="c-media",
        content="look",
        media=[str(img_a), str(img_b)],
    )
    with pytest.raises(RuntimeError, match="interrupt"):
        await loop._process_message(msg)

    loop.sessions.invalidate(UNIFIED_SESSION_KEY)
    persisted = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [m["role"] for m in persisted.messages] == ["user"]
    assert persisted.messages[0]["content"] == "look"
    assert persisted.messages[0]["media"] == [str(img_a), str(img_b)]


@pytest.mark.asyncio
async def test_process_message_persists_media_only_turn_without_text(tmp_path: Path) -> None:
    """A turn with images but no text still persists (previously silent-dropped).

    The old early-persist gate skipped messages without text, leaving pure
    image turns un-checkpointed. They now materialise as an empty-content
    user row with ``media`` attached.
    """
    img = tmp_path / "only.png"
    img.write_bytes(_PNG_1X1)

    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    msg = InboundMessage(
        channel="websocket",
        sender_id="u1",
        chat_id="c-images-only",
        content="",
        media=[str(img)],
    )
    with pytest.raises(RuntimeError):
        await loop._process_message(msg)

    loop.sessions.invalidate(UNIFIED_SESSION_KEY)
    persisted = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert len(persisted.messages) == 1
    assert persisted.messages[0]["role"] == "user"
    assert persisted.messages[0]["content"] == ""
    assert persisted.messages[0]["media"] == [str(img)]


@pytest.mark.asyncio
async def test_process_message_does_not_duplicate_early_persisted_user_message(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop._run_agent_loop = AsyncMock(return_value=(
        "done",
        None,
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "done"},
        ],
        "stop",
        False,
    ))  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u1", chat_id="c2", content="hello")
    )

    assert result.message is not None
    assert result.text == "done"
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [
        {k: v for k, v in m.items() if k in {"role", "content"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "done"},
    ]
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata


@pytest.mark.asyncio
async def test_internal_continuation_queues_turn_without_fake_user_history(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish the long goal.",
    }
    loop.sessions.save(session)

    calls: list[dict] = []

    async def fake_run_agent_loop(initial_messages, *, metadata=None, **_kwargs):
        calls.append({"initial_messages": initial_messages, "metadata": metadata})
        if len(calls) == 1:
            return (
                "paused",
                [],
                [*initial_messages, {"role": "assistant", "content": "paused"}],
                    "max_iterations",
                    False,
                )
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
                "completed",
                False,
            )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]
    pending: asyncio.Queue[InboundMessage] = asyncio.Queue()

    first = await loop._process_message(
        InboundMessage(
            channel="websocket",
            sender_id="u1",
            chat_id="c-auto",
            content="start the goal",
        ),
        pending_queue=pending,
    )

    assert first.message is None
    queued = pending.get_nowait()
    assert queued.sender_id == "system:continuation"
    assert queued.metadata[INTERNAL_CONTINUATION_META] is True
    assert "Finish the long goal." in queued.content

    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [
        {k: v for k, v in m.items() if k in {"role", "content"}}
        for m in session.messages
    ] == [{"role": "user", "content": "start the goal"}]

    second = await loop._process_message(queued, pending_queue=asyncio.Queue())

    assert second.message is not None
    assert second.text == "done"
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [
        {k: v for k, v in m.items() if k in {"role", "content"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "start the goal"},
        {"role": "assistant", "content": "done"},
    ]


@pytest.mark.asyncio
async def test_internal_continuation_preserves_streaming_route_metadata(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish the streamed long goal.",
    }
    loop.sessions.save(session)

    calls = 0

    async def fake_run_agent_loop(initial_messages, *, on_stream=None, on_stream_end=None, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (
                "paused",
                [],
                [*initial_messages, {"role": "assistant", "content": "paused"}],
                    "max_iterations",
                    False,
                )
        assert on_stream is not None
        assert on_stream_end is not None
        await on_stream("done")
        await on_stream_end(resuming=False)
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "completed",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    await loop._dispatch(InboundMessage(
        channel="other-channel",
        sender_id="u1",
        chat_id="c-stream",
        content="start the goal",
        metadata={
            "_wants_stream": True,
            "message_id": "om_001",
            "origin_message_id": "root_001",
            "_stream_id": "old-stream",
        },
    ))

    # Il canale esterno sulla sessione unificata proietta eventi di vista WebUI
    # (eco utente, run status), ma nessuna risposta finale finché il turno è in
    # pausa per la continuation.
    projected = []
    while loop.bus.outbound_size:
        projected.append(await loop.bus.consume_outbound())
    assert all(
        m.metadata.get("_user_echo") or m.metadata.get("_goal_status")
        for m in projected
    )
    queued = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=0.5)
    assert queued.metadata[INTERNAL_CONTINUATION_META] is True
    assert queued.metadata["_wants_stream"] is True
    assert queued.metadata["message_id"] == "om_001"
    assert queued.metadata["origin_message_id"] == "root_001"
    assert "_stream_id" not in queued.metadata

    await loop._dispatch(queued)

    outbound = []
    while loop.bus.outbound_size:
        outbound.append(await loop.bus.consume_outbound())
    # Gli eventi di proiezione della vista non interessano questo test.
    outbound = [
        m for m in outbound
        if not (m.metadata.get("_user_echo") or m.metadata.get("_goal_status"))
    ]
    deltas = [m for m in outbound if m.metadata.get("_stream_delta")]
    ends = [m for m in outbound if m.metadata.get("_stream_end")]
    streamed_markers = [m for m in outbound if m.metadata.get("_streamed")]

    assert [m.content for m in deltas] == ["done"]
    assert len(ends) == 1
    assert ends[0].metadata["_resuming"] is False
    assert ends[0].metadata["message_id"] == "om_001"
    assert ends[0].metadata["origin_message_id"] == "root_001"
    assert isinstance(ends[0].metadata.get("_stream_id"), str)
    assert streamed_markers and streamed_markers[-1].content == "done"


@pytest.mark.asyncio
async def test_websocket_internal_continuation_keeps_single_visible_run(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "Finish the long goal.",
    }
    loop.sessions.save(session)

    calls = 0

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (
                "paused",
                [],
                [*initial_messages, {"role": "assistant", "content": "paused"}],
                    "max_iterations",
                    False,
                )
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "completed",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    await loop._dispatch(InboundMessage(
        channel="websocket",
        sender_id="u1",
        chat_id="c-auto",
        content="start the goal",
        metadata={"webui": True},
    ))

    first_outbound = []
    while loop.bus.outbound_size:
        first_outbound.append(await loop.bus.consume_outbound())
    first_statuses = [m.metadata for m in first_outbound if m.metadata.get("_goal_status")]
    assert [m["goal_status"] for m in first_statuses] == ["running"]
    assert not [m for m in first_outbound if m.metadata.get("_turn_end")]
    started_at = first_statuses[0]["started_at"]

    queued = await asyncio.wait_for(loop.bus.consume_inbound(), timeout=0.5)
    assert queued.metadata[INTERNAL_CONTINUATION_META] is True
    assert queued.metadata[INTERNAL_CONTINUATION_RUN_STARTED_AT_META] == started_at

    await loop._dispatch(queued)

    second_outbound = []
    while loop.bus.outbound_size:
        second_outbound.append(await loop.bus.consume_outbound())
    second_statuses = [m.metadata for m in second_outbound if m.metadata.get("_goal_status")]
    assert [m["goal_status"] for m in second_statuses] == ["running", "idle"]
    assert second_statuses[0]["started_at"] == started_at
    turn_end = [m for m in second_outbound if m.metadata.get("_turn_end")]
    assert len(turn_end) == 1
    assert isinstance(turn_end[0].metadata.get("latency_ms"), int)


@pytest.mark.asyncio
async def test_process_message_uses_context_chat_id_for_runtime_prompt(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.context.build_messages = MagicMock(  # type: ignore[method-assign]
        return_value=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "runtime + hello"},
        ]
    )
    loop._run_agent_loop = AsyncMock(return_value=(  # type: ignore[method-assign]
        "done",
        [],
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "runtime + hello"},
            {"role": "assistant", "content": "done"},
        ],
        "stop",
        False,
    ))

    result = await loop._process_message(
        InboundMessage(
            channel="websocket",
            sender_id="u1",
            chat_id="thread-777",
            content="hello",
            metadata={"context_chat_id": "parent-456"},
            session_key_override="websocket:parent-456:thread:thread-777",
        )
    )

    assert result.message is not None
    assert result.message.chat_id == "thread-777"
    assert loop.context.build_messages.call_args.kwargs["chat_id"] == "parent-456"
    assert loop._run_agent_loop.call_args.kwargs["chat_id"] == "thread-777"


@pytest.mark.asyncio
async def test_process_message_uses_explicit_session_metadata_for_goal_context(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    chat_session = loop.sessions.get_or_create("websocket:chat-with-goal")
    chat_session.metadata[GOAL_STATE_KEY] = {
        "status": "active",
        "objective": "This chat goal must not leak into system.",
    }
    loop.sessions.save(chat_session)
    system_session = loop.sessions.get_or_create(_OTHER_KEY)
    system_session.metadata = {}
    loop.sessions.save(system_session)

    loop.context.build_messages = MagicMock(  # type: ignore[method-assign]
        return_value=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "runtime + system"},
        ]
    )
    loop._run_agent_loop = AsyncMock(return_value=(  # type: ignore[method-assign]
        "ok",
        [],
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "runtime + system"},
            {"role": "assistant", "content": "ok"},
        ],
        "stop",
        False,
    ))

    result = await loop._process_message(
        InboundMessage(
            channel="websocket",
            sender_id="system",
            chat_id="chat-with-goal",
            content="system work",
        ),
        session_key=_OTHER_KEY,
    )

    assert result.message is not None
    assert result.text == "ok"
    kwargs = loop.context.build_messages.call_args.kwargs
    assert kwargs["chat_id"] == "chat-with-goal"
    assert kwargs["session_metadata"] is system_session.metadata
    assert GOAL_STATE_KEY not in kwargs["session_metadata"]


@pytest.mark.asyncio
async def test_run_agent_loop_goal_continue_message_reads_latest_metadata(
    tmp_path: Path,
) -> None:
    from jenny.agent.runner import AgentRunResult

    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("websocket:late-goal")
    seen: dict[str, str | None] = {}

    async def fake_run(spec):
        assert callable(spec.goal_continue_message)
        session.metadata[GOAL_STATE_KEY] = {
            "status": "active",
            "objective": "Goal created during this runner call.",
        }
        seen["goal_continue"] = spec.goal_continue_message()
        return AgentRunResult(
            final_content="ok",
            messages=[{"role": "assistant", "content": "ok"}],
        )

    loop.runner.run = fake_run  # type: ignore[method-assign]

    await loop._run_agent_loop(
        [],
        session=session,
        channel="websocket",
        chat_id="late-goal",
        session_key=session.key,
    )

    assert "Goal created during this runner call." in (seen["goal_continue"] or "")


@pytest.mark.asyncio
async def test_process_direct_skip_user_persist_does_not_save_retry_user(
    tmp_path: Path,
) -> None:
    loop = _make_full_loop(tmp_path)
    session = loop.sessions.get_or_create("api:default")
    session.add_message("user", "hello")
    session.add_message("assistant", "previous empty-response attempt")
    loop.sessions.save(session)

    await loop.process_direct(
        "hello",
        session_key=session.key,
        channel="api",
        chat_id="default",
        persist_user_message=False,
    )

    session = loop.sessions.get_or_create("api:default")
    assert [(m["role"], m["content"]) for m in session.messages] == [
        ("user", "hello"),
        ("assistant", "previous empty-response attempt"),
        ("assistant", "Test title"),
    ]


@pytest.mark.asyncio
async def test_process_direct_appends_notice_when_images_stripped(tmp_path: Path) -> None:
    """When the provider's image-unsupported fallback fires (LLMResponse.images_stripped),
    the persisted/returned reply must carry a visible notice instead of silently
    looking like the attached image was simply ignored."""
    loop = _make_full_loop(tmp_path)
    loop.provider.chat_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value=LLMResponse(content="Ecco la risposta", images_stripped=True)
    )

    # Chiave nel vocabolario di ``jenny.session.keys``: con un prefisso non
    # registrato (era ``api:``, un canale che non esiste) da T4.10 il turno cade
    # su ``internal`` e ``resolve_turn_visibility`` lo rende SILENT su un canale
    # utente, cioe' ``process_direct`` non torna niente. Qui il soggetto e'
    # l'avviso, non la visibilita'.
    result = await loop.process_direct(
        "descrivi questa immagine",
        session_key="websocket:vision-test",
        channel="websocket",
        chat_id="vision-test",
    )

    assert result is not None
    assert result.content is not None
    assert result.content.startswith("Ecco la risposta")
    assert "non supporta input visivi" in result.content

    session = loop.sessions.get_or_create("websocket:vision-test")
    assistant_messages = [m["content"] for m in session.messages if m["role"] == "assistant"]
    assert assistant_messages
    assert "non supporta input visivi" in assistant_messages[-1]


@pytest.mark.asyncio
async def test_process_direct_no_notice_when_images_not_stripped(tmp_path: Path) -> None:
    """Normal replies (no fallback triggered) must not gain the vision-unsupported notice."""
    loop = _make_full_loop(tmp_path)
    loop.provider.chat_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value=LLMResponse(content="Ecco la risposta", images_stripped=False)
    )

    result = await loop.process_direct(
        "ciao",
        session_key="websocket:vision-test-2",
        channel="websocket",
        chat_id="vision-test-2",
    )

    assert result is not None
    assert result.content == "Ecco la risposta"


def test_set_tool_context_uses_effective_key_for_spawn_tool(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    spawn_tool = loop.tools.get("spawn")
    assert spawn_tool is not None

    loop._set_tool_context(
        "websocket",
        "thread-777",
        session_key="websocket:parent-456:thread:thread-777",
    )

    assert spawn_tool._origin_channel.get() == "websocket"  # type: ignore[attr-defined]
    assert spawn_tool._origin_chat_id.get() == "thread-777"  # type: ignore[attr-defined]
    assert spawn_tool._session_key.get() == "websocket:parent-456:thread:thread-777"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_next_turn_after_crash_closes_pending_user_turn_before_new_input(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]
    loop.provider.chat_with_retry = AsyncMock(return_value=MagicMock())  # unused because _run_agent_loop is stubbed

    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    session.add_message("user", "old question")
    session.metadata[AgentLoop._PENDING_USER_TURN_KEY] = True
    loop.sessions.save(session)

    loop._run_agent_loop = AsyncMock(return_value=(
        "new answer",
        None,
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "Error: Task interrupted before a response was generated."},
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
        ],
        "stop",
        False,
    ))  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u1", chat_id="c3", content="new question")
    )

    assert result.message is not None
    assert result.text == "new answer"
    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [
        {k: v for k, v in m.items() if k in {"role", "content"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "Error: Task interrupted before a response was generated."},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata


@pytest.mark.asyncio
async def test_stop_preserves_runtime_checkpoint_for_next_turn(tmp_path: Path) -> None:
    """/stop materializza SUBITO il checkpoint del turno interrotto nella
    history (restore sincrono in cmd_stop, deterministico anche con task
    abbandonati); il turno successivo riparte dal contesto già ripristinato."""
    from jenny.command.builtin import cmd_stop
    from jenny.command.router import CommandContext

    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    checkpoint_saved = asyncio.Event()

    async def interrupted_run_agent_loop(_initial_messages, *, session=None, **_kwargs):
        assert session is not None
        loop._set_runtime_checkpoint(
            session,
            {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "python_exec", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "python_exec", "arguments": "{}"},
                    }
                ],
            },
        )
        checkpoint_saved.set()
        await asyncio.Event().wait()

    loop._run_agent_loop = interrupted_run_agent_loop  # type: ignore[method-assign]

    first_msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c4", content="keep progress")
    task = asyncio.create_task(loop._process_message(first_msg))
    loop._active_tasks[first_msg.session_key] = [task]
    await asyncio.wait_for(checkpoint_saved.wait(), timeout=1.0)

    stop_msg = InboundMessage(channel="websocket", sender_id="u1", chat_id="c4", content="/stop")
    stop_ctx = CommandContext(msg=stop_msg, session=None, key=stop_msg.session_key, raw="/stop", loop=loop)
    stop_result = await cmd_stop(stop_ctx)

    assert "Stopped 1 task" in stop_result.content
    assert task.done()

    loop.sessions.invalidate(UNIFIED_SESSION_KEY)
    interrupted = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    # /stop ha già materializzato il checkpoint in history e ripulito i metadata.
    assert AgentLoop._PENDING_USER_TURN_KEY not in interrupted.metadata
    assert AgentLoop._RUNTIME_CHECKPOINT_KEY not in interrupted.metadata
    assert [m.get("role") for m in interrupted.messages] == [
        "user", "assistant", "tool", "tool",
    ]

    async def resumed_run_agent_loop(initial_messages, **_kwargs):
        return (
            "next answer",
            None,
            [*initial_messages, {"role": "assistant", "content": "next answer"}],
            "stop",
            False,
        )

    loop._run_agent_loop = resumed_run_agent_loop  # type: ignore[method-assign]
    result = await loop._process_message(
        InboundMessage(channel="websocket", sender_id="u1", chat_id="c4", content="continue here")
    )

    assert result.message is not None
    assert result.text == "next answer"

    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    assert [
        {k: v for k, v in m.items() if k in {"role", "content", "tool_call_id", "name"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "keep progress"},
        {"role": "assistant", "content": "working"},
        {"role": "tool", "tool_call_id": "call_done", "name": "read_file", "content": "ok"},
        {
            "role": "tool",
            "tool_call_id": "call_pending",
            "name": "python_exec",
            "content": "Error: Task interrupted before this tool finished.",
        },
        {"role": "user", "content": "continue here"},
        {"role": "assistant", "content": "next answer"},
    ]
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata
    assert AgentLoop._RUNTIME_CHECKPOINT_KEY not in session.metadata


@pytest.mark.asyncio
async def test_system_subagent_followup_is_persisted_before_prompt_assembly(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("internal:test")
    session.add_message("user", "question")
    session.add_message("assistant", "working")
    loop.sessions.save(session)

    seen: dict[str, list[dict]] = {}

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        seen["initial_messages"] = initial_messages
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="internal:test",
            content="subagent result",
            metadata={"subagent_task_id": "sub-1"},
        )
    )

    non_system = [m for m in seen["initial_messages"] if m.get("role") != "system"]
    assert "question" in non_system[0]["content"]
    assert "working" in non_system[1]["content"]
    # User turns carry the timestamp prefix so the model can reason about
    # relative time. Assistant turns do NOT, otherwise the model treats those
    # past replies as in-context examples and starts its own outputs with
    # ``[Message Time: ...]`` (which then leaks back to the user).
    assert "[Message Time:" in non_system[0]["content"]
    assert "[Message Time:" not in non_system[1]["content"]
    assert non_system[2]["content"].count("subagent result") == 1
    assert "Current Time:" in non_system[2]["content"]

    loop.sessions.invalidate("internal:test")
    persisted = loop.sessions.get_or_create("internal:test")
    assert [
        {k: v for k, v in m.items() if k in {"role", "content", "injected_event", "subagent_task_id"}}
        for m in persisted.messages
    ] == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "working"},
        {
            "role": "assistant",
            "content": "subagent result",
            "injected_event": "subagent_result",
            "subagent_task_id": "sub-1",
        },
        {"role": "assistant", "content": "done"},
    ]


@pytest.mark.asyncio
async def test_system_turn_stamps_goal_last_turn_at(tmp_path: Path) -> None:
    """Un turno di sistema (subagent) deve confluire nella stessa persistenza
    della FSM e stampare ``last_turn_at`` sul goal sostenuto attivo, così un
    turno di background non fa scadere prematuramente il goal."""
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create("internal:goal")
    session.metadata[GOAL_STATE_KEY] = {"status": "active", "objective": "keep going"}
    session.add_message("user", "start the goal")
    loop.sessions.save(session)
    assert "last_turn_at" not in session.metadata[GOAL_STATE_KEY]

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        return ("ack", [], [*initial_messages, {"role": "assistant", "content": "ack"}], "stop", False)

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="internal:goal",
            content="subagent result",
            metadata={"subagent_task_id": "sub-1"},
        )
    )

    loop.sessions.invalidate("internal:goal")
    persisted = loop.sessions.get_or_create("internal:goal")
    goal = persisted.metadata[GOAL_STATE_KEY]
    assert goal["status"] == "active"
    assert goal.get("last_turn_at"), "il turno di sistema deve stampare last_turn_at"


@pytest.mark.asyncio
async def test_multiple_subagent_followups_all_persist_as_standalone_history(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        return (
            "ack",
            [],
            [*initial_messages, {"role": "assistant", "content": "ack"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    for idx in range(3):
        await loop._process_message(
            InboundMessage(
                channel="system",
                sender_id="subagent",
                chat_id="internal:multi",
                content=f"subagent result {idx}",
                metadata={"subagent_task_id": f"sub-{idx}"},
            )
        )

    loop.sessions.invalidate("internal:multi")
    persisted = loop.sessions.get_or_create("internal:multi")
    followups = [m for m in persisted.messages if m.get("injected_event") == "subagent_result"]
    assert [m["content"] for m in followups] == [
        "subagent result 0",
        "subagent result 1",
        "subagent result 2",
    ]


def test_prompt_merge_does_not_replace_standalone_subagent_history_entry(tmp_path: Path) -> None:
    loop = _mk_loop()
    session = Session(key="internal:merge")
    session.add_message("assistant", "previous assistant")

    inserted = loop._persist_subagent_followup(
        session,
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="internal:merge",
            content="subagent result",
            metadata={"subagent_task_id": "sub-1"},
        ),
    )

    assert inserted is True

    builder = ContextBuilder(tmp_path)
    projected = builder.build_messages(
        history=session.get_history(max_messages=0),
        current_message="",
        current_role="assistant",
        channel="internal",
        chat_id="merge",
    )

    non_system = [m for m in projected if m.get("role") != "system"]
    assert len(non_system) == 2
    assert "subagent result" in non_system[-1]["content"]
    assert session.messages[-1]["content"] == "subagent result"
    assert session.messages[-1]["injected_event"] == "subagent_result"


def test_subagent_followup_dedupes_by_task_id() -> None:
    loop = _mk_loop()
    session = Session(key="internal:dedupe")
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="internal:dedupe",
        content="subagent result",
        metadata={"subagent_task_id": "sub-1"},
    )

    assert loop._persist_subagent_followup(session, msg) is True
    assert loop._persist_subagent_followup(session, msg) is False
    assert len(session.messages) == 1


def test_subagent_followup_skips_empty_content() -> None:
    loop = _mk_loop()
    session = Session(key="internal:empty")
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="internal:empty",
        content="",
        metadata={"subagent_task_id": "sub-empty"},
    )

    assert loop._persist_subagent_followup(session, msg) is False
    assert session.messages == []


def test_set_tool_context_passes_thread_session_key_to_spawn(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)

    loop._set_tool_context(
        "websocket",
        "C123",
        message_id="msg-123",
        metadata={"thread": {"id": "1700.42", "kind": "channel"}},
        session_key="websocket:C123:1700.42",
    )

    spawn_tool = loop.tools.get("spawn")
    assert spawn_tool is not None
    assert spawn_tool._session_key.get() == "websocket:C123:1700.42"
    assert spawn_tool._origin_message_id.get() == "msg-123"


@pytest.mark.asyncio
async def test_system_subagent_followup_uses_thread_session_and_metadata(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    thread_session = loop.sessions.get_or_create("websocket:C123:1700.42")
    thread_session.add_message("user", "thread question")
    loop.sessions.save(thread_session)

    seen: dict[str, list[dict]] = {}

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        seen["initial_messages"] = initial_messages
        return (
            "done",
            [],
            [*initial_messages, {"role": "assistant", "content": "done"}],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    outbound = await loop._process_message(
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="websocket:C123",
            content="subagent result",
            session_key_override="websocket:C123:1700.42",
            metadata={"subagent_task_id": "sub-1", "origin_message_id": "msg-123"},
        )
    )

    assert outbound.message is not None
    assert outbound.message.channel == "websocket"
    assert outbound.message.chat_id == "C123"
    assert outbound.message.metadata == {
        "origin_message_id": "msg-123",
    }
    assert "thread question" in seen["initial_messages"][1]["content"]

    loop.sessions.invalidate("websocket:C123:1700.42")
    persisted = loop.sessions.get_or_create("websocket:C123:1700.42")
    assert any(m.get("subagent_task_id") == "sub-1" for m in persisted.messages)


@pytest.mark.asyncio
async def test_turn_after_unanswered_user_keeps_tool_call_pairing(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.consolidator.maybe_consolidate_by_tokens = AsyncMock(return_value=False)  # type: ignore[method-assign]

    session = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)
    session.add_message("user", "earlier question that never got an answer")
    loop.sessions.save(session)

    async def fake_run_agent_loop(initial_messages, **_kwargs):
        assert [m["role"] for m in initial_messages] == ["system", "user"]
        return (
            "done",
            [],
            [
                *initial_messages,
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_ls",
                        "type": "function",
                        "function": {"name": "python_exec", "arguments": '{"command": "ls"}'},
                    }],
                },
                {"role": "tool", "tool_call_id": "call_ls", "name": "python_exec", "content": "file.txt"},
                {"role": "assistant", "content": "done"},
            ],
            "stop",
            False,
        )

    loop._run_agent_loop = fake_run_agent_loop  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(
            channel="websocket", sender_id="u1", chat_id="c-merge", content="and another thing"
        )
    )

    assert result.message is not None
    loop.sessions.invalidate(UNIFIED_SESSION_KEY)
    persisted = loop.sessions.get_or_create(UNIFIED_SESSION_KEY)

    declared: set[str] = set()
    for message in persisted.messages:
        if message.get("role") == "assistant":
            declared.update(
                str(tc["id"]) for tc in message.get("tool_calls") or [] if tc.get("id")
            )
        if message.get("role") == "tool":
            assert str(message.get("tool_call_id")) in declared, (
                f"orphaned tool result {message.get('tool_call_id')!r}: "
                f"{[m.get('role') for m in persisted.messages]}"
            )
    assert [m["role"] for m in persisted.messages] == [
        "user", "user", "assistant", "tool", "assistant",
    ]


def test_save_turn_keeps_placeholder_for_empty_tool_result_blocks() -> None:
    loop = _mk_loop()
    session = Session(key="test:empty-tool-blocks")

    loop._save_turn(
        session,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_empty",
                    "type": "function",
                    "function": {"name": "python_exec", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_empty", "name": "python_exec", "content": []},
        ],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["assistant", "tool"]
    assert session.messages[1]["content"] == [
        {"type": "text", "text": "[tool result omitted during persistence]"}
    ]


@pytest.mark.parametrize(
    ("key", "tool_result"),
    [
        pytest.param(
            "test:orphan-guard",
            {"role": "tool", "tool_call_id": "call_ghost", "name": "python_exec", "content": "boo"},
            id="orphaned_tool_results",
        ),
        pytest.param(
            "test:missing-tool-call-id",
            {"role": "tool", "name": "python_exec", "content": "missing id"},
            id="tool_results_without_tool_call_id",
        ),
    ],
)
def test_save_turn_drops(key: str, tool_result: dict) -> None:
    """Un risultato di tool senza la chiamata che lo dichiara — un id che
    nessuno ha chiesto, o nessun id — non entra nella storia."""
    loop = _mk_loop()
    session = Session(key=key)
    session.add_message("user", "hi")

    loop._save_turn(
        session,
        [tool_result, {"role": "assistant", "content": "done"}],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["user", "assistant"]


def test_save_turn_keeps_tool_results_declared_in_prior_history() -> None:
    loop = _mk_loop()
    session = Session(key="test:prior-declared")
    session.add_message(
        "assistant",
        "working",
        tool_calls=[{
            "id": "call_prior",
            "type": "function",
            "function": {"name": "python_exec", "arguments": "{}"},
        }],
    )

    loop._save_turn(
        session,
        [{"role": "tool", "tool_call_id": "call_prior", "name": "python_exec", "content": "ok"}],
        skip=0,
    )

    assert [m["role"] for m in session.messages] == ["assistant", "tool"]
