"""Tests for append-only WebUI transcript replay."""

from __future__ import annotations

from jenny.webui.transcript import (
    WEBUI_TRANSCRIPT_SCHEMA_VERSION,
    append_transcript_object,
    build_webui_thread_response,
    read_transcript_lines,
    replay_transcript_to_ui_messages,
)


def test_append_and_read_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t1"
    append_transcript_object(key, {"event": "user", "chat_id": "t1", "text": "hello"})
    lines = read_transcript_lines(key)
    assert len(lines) == 1
    assert lines[0]["text"] == "hello"


def _force_small_chunks(monkeypatch, *, turns: int = 2) -> None:
    """Pochi turni per chunk, così sei turni fanno tre chunk e la paginazione li attraversa."""
    monkeypatch.setattr("jenny.webui.transcript_store.TURNS_PER_CHUNK", turns)


def _append_numbered_turn(key: str, chat_id: str, idx: int) -> None:
    append_transcript_object(
        key,
        {"event": "user", "chat_id": chat_id, "text": f"question {idx} " + ("x" * 24)},
    )
    append_transcript_object(
        key,
        {"event": "message", "chat_id": chat_id, "text": f"answer {idx} " + ("y" * 24)},
    )
    append_transcript_object(key, {"event": "turn_end", "chat_id": chat_id})


def _write_segmented_turns(tmp_path, monkeypatch, key: str, chat_id: str, count: int) -> None:
    from jenny.webui.transcript import delete_webui_transcript

    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    _force_small_chunks(monkeypatch)
    delete_webui_transcript(key)
    for idx in range(1, count + 1):
        _append_numbered_turn(key, chat_id, idx)


def _message_contents(payload: dict) -> list[str]:
    return [str(message.get("content") or "") for message in payload["messages"]]


def _numbered_turn_texts(start: int, end: int) -> list[str]:
    return [
        text
        for idx in range(start, end + 1)
        for text in (f"question {idx} " + ("x" * 24), f"answer {idx} " + ("y" * 24))
    ]


def test_a_transcript_in_several_chunks_reads_whole(tmp_path, monkeypatch) -> None:
    from jenny.webui.transcript_store import _read_segment_manifest_entries

    key = "websocket:segmented"
    _write_segmented_turns(tmp_path, monkeypatch, key, "segmented", 6)

    assert [e["turn_count"] for e in _read_segment_manifest_entries(key)] == [2, 2]

    lines = read_transcript_lines(key)
    contents = [str(line.get("text") or "") for line in lines if line.get("event") in {"user", "message"}]
    assert contents == _numbered_turn_texts(1, 6)


def test_segmented_transcript_paginates_latest_and_older_without_overlap(
    tmp_path,
    monkeypatch,
) -> None:
    key = "websocket:paged"
    _write_segmented_turns(tmp_path, monkeypatch, key, "paged", 6)

    latest = build_webui_thread_response(key, limit=4)
    assert latest is not None
    assert latest["page"]["has_more_before"] is True
    assert latest["page"]["user_message_offset"] == 4
    assert _message_contents(latest) == _numbered_turn_texts(5, 6)

    older = build_webui_thread_response(
        key,
        limit=4,
        before=latest["page"]["before_cursor"],
    )
    assert older is not None
    assert older["page"]["user_message_offset"] == 2
    assert _message_contents(older) == _numbered_turn_texts(3, 4)


def test_page_cursor_survives_active_rotation_after_latest_page(
    tmp_path,
    monkeypatch,
) -> None:
    key = "websocket:stable-cursor"
    _write_segmented_turns(tmp_path, monkeypatch, key, "stable-cursor", 7)

    latest = build_webui_thread_response(key, limit=4)
    assert latest is not None
    cursor = latest["page"]["before_cursor"]
    assert cursor
    assert _message_contents(latest) == _numbered_turn_texts(6, 7)

    for idx in range(8, 13):
        _append_numbered_turn(key, "stable-cursor", idx)

    older = build_webui_thread_response(key, limit=4, before=cursor)

    assert older is not None
    assert _message_contents(older) == _numbered_turn_texts(4, 5)


def test_delete_webui_transcript_removes_every_chunk(tmp_path, monkeypatch) -> None:
    from jenny.webui.transcript import delete_webui_transcript

    key = "websocket:delete-segments"
    _write_segmented_turns(tmp_path, monkeypatch, key, "delete-segments", 4)

    assert delete_webui_transcript(key) is True
    assert read_transcript_lines(key) == []
    assert delete_webui_transcript(key) is False


def test_replay_delta_and_turn_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t2"
    for ev in (
        {"event": "user", "chat_id": "t2", "text": "q"},
        {"event": "reasoning_delta", "chat_id": "t2", "text": "think"},
        {"event": "reasoning_end", "chat_id": "t2"},
        {"event": "delta", "chat_id": "t2", "text": "a"},
        {"event": "stream_end", "chat_id": "t2"},
        {"event": "turn_end", "chat_id": "t2", "latency_ms": 42},
    ):
        append_transcript_object(key, ev)
    lines = read_transcript_lines(key)
    msgs = replay_transcript_to_ui_messages(lines)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "q"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "a"
    assert msgs[1]["reasoning"] == "think"
    assert msgs[1]["latencyMs"] == 42


def test_thread_response_does_not_mark_completed_message_tool_tail_pending(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:cron-tail"
    turn_id = "cron:job:run"
    for ev in (
        {
            "event": "message",
            "chat_id": "cron-tail",
            "text": 'message({"content":"Cron test"})',
            "kind": "tool_hint",
            "tool_events": [{
                "phase": "start",
                "call_id": "call-message",
                "name": "message",
                "arguments": {"content": "Cron test"},
            }],
            "turn_id": turn_id,
            "turn_phase": "activity",
            "turn_seq": 5,
        },
        {
            "event": "message",
            "chat_id": "cron-tail",
            "text": "Cron test",
            "source": {"kind": "cron", "label": "one-min-test"},
            "turn_id": turn_id,
            "turn_phase": "answer",
            "turn_seq": 6,
        },
        {
            "event": "message",
            "chat_id": "cron-tail",
            "text": "",
            "kind": "progress",
            "tool_events": [{
                "phase": "end",
                "call_id": "call-message",
                "name": "message",
                "arguments": {"content": "Cron test"},
                "result": "ok",
            }],
            "turn_id": turn_id,
            "turn_phase": "activity",
            "turn_seq": 7,
        },
        {
            "event": "turn_end",
            "chat_id": "cron-tail",
            "turn_id": turn_id,
            "turn_phase": "complete",
            "turn_seq": 8,
        },
    ):
        append_transcript_object(key, ev)

    out = build_webui_thread_response(key)

    assert out is not None
    assert out["has_pending_tool_calls"] is False
    assert out["messages"][-1]["kind"] == "trace"
    assert out["messages"][-2]["content"] == "Cron test"


def test_thread_response_marks_unfinished_tool_tail_pending(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:active-tail"
    append_transcript_object(
        key,
        {
            "event": "message",
            "chat_id": "active-tail",
            "text": 'python_exec({"code":"date"})',
            "kind": "tool_hint",
        },
    )

    out = build_webui_thread_response(key)

    assert out is not None
    assert out["has_pending_tool_calls"] is True


def test_replay_preserves_turn_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-turn"
    for ev in (
        {
            "event": "user",
            "chat_id": "t-turn",
            "text": "q",
            "turn_id": "turn-1",
            "turn_phase": "user",
            "turn_seq": 1,
        },
        {
            "event": "reasoning_delta",
            "chat_id": "t-turn",
            "text": "think",
            "turn_id": "turn-1",
            "turn_phase": "reasoning",
            "turn_seq": 2,
        },
        {
            "event": "delta",
            "chat_id": "t-turn",
            "text": "a",
            "turn_id": "turn-1",
            "turn_phase": "answer",
            "turn_seq": 3,
        },
        {
            "event": "turn_end",
            "chat_id": "t-turn",
            "latency_ms": 12,
            "turn_id": "turn-1",
            "turn_phase": "complete",
            "turn_seq": 4,
        },
    ):
        append_transcript_object(key, ev)

    msgs = replay_transcript_to_ui_messages(read_transcript_lines(key))

    assert msgs[0]["turnId"] == "turn-1"
    assert msgs[0]["turnPhase"] == "user"
    assert msgs[0]["turnSeq"] == 1
    assert msgs[1]["turnId"] == "turn-1"
    assert msgs[1]["turnPhase"] == "answer"
    assert msgs[1]["turnSeq"] == 3


def test_replay_reused_turn_id_after_turn_end_starts_new_turn(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-reused-turn"

    def event(
        event: str,
        phase: str,
        seq: int,
        text: str | None = None,
    ) -> dict[str, object]:
        out = {
            "event": event,
            "chat_id": "t-reused-turn",
            "turn_id": "turn-1",
            "turn_phase": phase,
            "turn_seq": seq,
        }
        if text is not None:
            out["text"] = text
        return out

    for record in (
        event("user", "user", 1, "remind me later"),
        event("message", "answer", 2, "Reminder set."),
        event("turn_end", "complete", 3),
        event("message", "answer", 1, "Time to drink water."),
        event("turn_end", "complete", 2),
    ):
        append_transcript_object(key, record)

    msgs = replay_transcript_to_ui_messages(read_transcript_lines(key))

    assert [m["content"] for m in msgs] == [
        "remind me later",
        "Reminder set.",
        "Time to drink water.",
    ]
    assert msgs[1]["turnId"] == "turn-1"
    assert msgs[2]["turnId"].startswith("turn-1:replay:")
    assert msgs[2]["turnId"] != msgs[1]["turnId"]


def test_build_response_restores_session_users_for_legacy_transcript(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:legacy-users"
    append_transcript_object(
        key,
        {"event": "message", "chat_id": "legacy-users", "text": "assistant one"},
    )
    append_transcript_object(key, {"event": "turn_end", "chat_id": "legacy-users"})
    append_transcript_object(
        key,
        {"event": "message", "chat_id": "legacy-users", "text": "assistant two"},
    )
    append_transcript_object(key, {"event": "turn_end", "chat_id": "legacy-users"})

    out = build_webui_thread_response(
        key,
        session_messages=[
            {"role": "user", "content": "prompt one", "timestamp": "2026-06-02T10:00:00"},
            {"role": "assistant", "content": "assistant one"},
            {"role": "user", "content": "prompt two", "timestamp": "2026-06-02T10:01:00"},
            {"role": "assistant", "content": "assistant two"},
        ],
    )

    assert out is not None
    assert [(m["role"], m["content"]) for m in out["messages"]] == [
        ("user", "prompt one"),
        ("assistant", "assistant one"),
        ("user", "prompt two"),
        ("assistant", "assistant two"),
    ]


def test_build_response_restores_session_users_without_duplicating_new_transcript_users(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:mixed-users"
    append_transcript_object(
        key,
        {"event": "message", "chat_id": "mixed-users", "text": "old assistant"},
    )
    append_transcript_object(key, {"event": "turn_end", "chat_id": "mixed-users"})
    append_transcript_object(key, {"event": "user", "chat_id": "mixed-users", "text": "new prompt"})
    append_transcript_object(
        key,
        {"event": "message", "chat_id": "mixed-users", "text": "new assistant"},
    )
    append_transcript_object(key, {"event": "turn_end", "chat_id": "mixed-users"})

    out = build_webui_thread_response(
        key,
        session_messages=[
            {"role": "user", "content": "old prompt"},
            {"role": "assistant", "content": "old assistant"},
            {"role": "user", "content": "new prompt"},
            {"role": "assistant", "content": "new assistant"},
        ],
    )

    assert out is not None
    assert [(m["role"], m["content"]) for m in out["messages"]] == [
        ("user", "old prompt"),
        ("assistant", "old assistant"),
        ("user", "new prompt"),
        ("assistant", "new assistant"),
    ]


def test_replay_augments_assistant_text() -> None:
    msgs = replay_transcript_to_ui_messages(
        [
            {"event": "user", "chat_id": "t-img", "text": "draw"},
            {"event": "delta", "chat_id": "t-img", "text": "![Diagram](diagram.png)"},
            {"event": "stream_end", "chat_id": "t-img"},
        ],
        augment_assistant_text=lambda text: text.replace("diagram.png", "/api/media/sig/payload"),
    )

    assert msgs[1]["content"] == "![Diagram](/api/media/sig/payload)"


def test_replay_uses_stream_end_final_text() -> None:
    msgs = replay_transcript_to_ui_messages(
        [
            {"event": "user", "chat_id": "t-img", "text": "draw"},
            {"event": "stream_end", "chat_id": "t-img", "text": "![Diagram](/api/media/sig/payload)"},
        ],
    )

    assert msgs[1]["content"] == "![Diagram](/api/media/sig/payload)"


def test_build_response_backfills_legacy_sse_only_transcripts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-legacy"
    for ev in (
        {"event": "delta", "chat_id": "t-legacy", "text": "first answer"},
        {"event": "stream_end", "chat_id": "t-legacy"},
        {"event": "turn_end", "chat_id": "t-legacy"},
        {"event": "message", "chat_id": "t-legacy", "text": "second answer"},
        {"event": "turn_end", "chat_id": "t-legacy"},
    ):
        append_transcript_object(key, ev)

    out = build_webui_thread_response(
        key,
        session_messages=[
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second answer"},
        ],
    )

    assert out is not None
    assert [message["role"] for message in out["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [message["content"] for message in out["messages"]] == [
        "first question",
        "first answer",
        "second question",
        "second answer",
    ]


def test_backfill_does_not_duplicate_existing_user_transcript(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-current"
    for ev in (
        {"event": "user", "chat_id": "t-current", "text": "already stored"},
        {"event": "message", "chat_id": "t-current", "text": "answer"},
        {"event": "turn_end", "chat_id": "t-current"},
    ):
        append_transcript_object(key, ev)

    out = build_webui_thread_response(
        key,
        session_messages=[{"role": "user", "content": "already stored"}],
    )

    assert out is not None
    assert [message["role"] for message in out["messages"]] == ["user", "assistant"]
    assert out["messages"][0]["content"] == "already stored"


def test_backfill_does_not_misalign_when_session_only_has_transcript_tail(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-tail"
    for ev in (
        {"event": "message", "chat_id": "t-tail", "text": "old answer"},
        {"event": "turn_end", "chat_id": "t-tail"},
        {"event": "message", "chat_id": "t-tail", "text": "tail answer"},
        {"event": "turn_end", "chat_id": "t-tail"},
    ):
        append_transcript_object(key, ev)

    out = build_webui_thread_response(
        key,
        session_messages=[
            {"role": "user", "content": "tail question"},
            {"role": "assistant", "content": "tail answer"},
        ],
    )

    assert out is not None
    assert [message["role"] for message in out["messages"]] == [
        "assistant",
        "user",
        "assistant",
    ]
    assert [message["content"] for message in out["messages"]] == [
        "old answer",
        "tail question",
        "tail answer",
    ]


def test_replay_infers_video_media_from_attachment_name() -> None:
    msgs = replay_transcript_to_ui_messages(
        [
            {"event": "user", "chat_id": "t-video", "text": "render"},
            {
                "event": "message",
                "chat_id": "t-video",
                "text": "video ready",
                "media_urls": [{"url": "/api/media/sig/payload", "name": "intro.mp4"}],
            },
        ],
    )

    assert msgs[1]["media"] == [
        {"kind": "video", "url": "/api/media/sig/payload", "name": "intro.mp4"},
    ]


def test_replay_resigns_assistant_media_paths_before_stale_urls() -> None:
    msgs = replay_transcript_to_ui_messages(
        [
            {"event": "user", "chat_id": "t-video-resign", "text": "render"},
            {
                "event": "message",
                "chat_id": "t-video-resign",
                "text": "video ready",
                "media": ["/tmp/intro.mp4"],
                "media_urls": [{"url": "/api/media/old-sig/old-payload", "name": "intro.mp4"}],
            },
        ],
        augment_assistant_media=lambda paths: [
            {"kind": "video", "url": f"/api/media/new-sig/{paths[0].split('/')[-1]}", "name": "intro.mp4"},
        ],
    )

    assert msgs[1]["media"] == [
        {"kind": "video", "url": "/api/media/new-sig/intro.mp4", "name": "intro.mp4"},
    ]


def test_replay_infers_svg_media_from_attachment_name() -> None:
    msgs = replay_transcript_to_ui_messages(
        [
            {"event": "user", "chat_id": "t-svg", "text": "send svg"},
            {
                "event": "message",
                "chat_id": "t-svg",
                "text": "chart ready",
                "media_urls": [{"url": "/api/media/sig/payload", "name": "chart.svg"}],
            },
        ],
    )

    assert msgs[1]["media"] == [
        {"kind": "image", "url": "/api/media/sig/payload", "name": "chart.svg"},
    ]


def test_replay_infers_file_media_from_attachment_name() -> None:
    msgs = replay_transcript_to_ui_messages(
        [
            {"event": "user", "chat_id": "t-file-media", "text": "send html"},
            {
                "event": "message",
                "chat_id": "t-file-media",
                "text": "file ready",
                "media_urls": [{"url": "/api/media/sig/payload", "name": "index.html"}],
            },
        ],
    )

    assert msgs[1]["media"] == [
        {"kind": "file", "url": "/api/media/sig/payload", "name": "index.html"},
    ]


def test_replay_file_edit_event_creates_file_activity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-file"
    for ev in (
        {"event": "user", "chat_id": "t-file", "text": "edit"},
        {
            "event": "message",
            "chat_id": "t-file",
            "text": 'write_file({"path":"foo.txt"})',
            "kind": "tool_hint",
        },
        {
            "event": "file_edit",
            "chat_id": "t-file",
            "edits": [
                {
                    "version": 1,
                    "call_id": "call-write",
                    "tool": "write_file",
                    "path": "foo.txt",
                    "phase": "end",
                    "added": 2,
                    "deleted": 1,
                    "approximate": False,
                    "status": "done",
                },
            ],
        },
    ):
        append_transcript_object(key, ev)

    msgs = replay_transcript_to_ui_messages(read_transcript_lines(key))

    assert len(msgs) == 3
    assert msgs[1]["kind"] == "trace"
    assert msgs[1]["traces"] == ['write_file({"path":"foo.txt"})']
    assert "fileEdits" not in msgs[1]
    assert msgs[2]["kind"] == "trace"
    assert msgs[2]["traces"] == []
    assert msgs[2]["fileEdits"] == [
        {
            "version": 1,
            "call_id": "call-write",
            "tool": "write_file",
            "path": "foo.txt",
            "phase": "end",
            "added": 2,
            "deleted": 1,
            "approximate": False,
            "status": "done",
        },
    ]
    assert msgs[2]["activitySegmentId"]
    assert msgs[2]["activitySegmentId"] != msgs[1]["activitySegmentId"]


def test_replay_file_edit_absorbs_matching_write_tool_event() -> None:
    msgs = replay_transcript_to_ui_messages([
        {
            "event": "message",
            "chat_id": "t-file",
            "text": 'write_file({"path":"foo.txt"})',
            "kind": "tool_hint",
            "tool_events": [
                {
                    "phase": "start",
                    "call_id": "call-write",
                    "name": "write_file",
                    "arguments": {"path": "foo.txt", "content": "hello\n"},
                },
            ],
        },
        {
            "event": "file_edit",
            "chat_id": "t-file",
            "edits": [
                {
                    "version": 1,
                    "call_id": "call-write",
                    "tool": "write_file",
                    "path": "foo.txt",
                    "phase": "start",
                    "added": 1,
                    "deleted": 0,
                    "approximate": True,
                    "status": "editing",
                },
            ],
        },
        {
            "event": "message",
            "chat_id": "t-file",
            "text": "",
            "kind": "progress",
            "tool_events": [
                {
                    "phase": "end",
                    "call_id": "call-write",
                    "name": "write_file",
                    "arguments": {"path": "foo.txt", "content": "hello\n"},
                    "result": "ok",
                },
            ],
        },
    ])

    assert len(msgs) == 1
    assert msgs[0]["kind"] == "trace"
    assert msgs[0]["traces"] == []
    assert "toolEvents" not in msgs[0]
    assert msgs[0]["fileEdits"] == [
        {
            "version": 1,
            "call_id": "call-write",
            "tool": "write_file",
            "path": "foo.txt",
            "phase": "start",
            "added": 1,
            "deleted": 0,
            "approximate": True,
            "status": "editing",
        },
    ]


def test_replay_keeps_interrupted_pre_tool_text_in_activity() -> None:
    msgs = replay_transcript_to_ui_messages([
        {"event": "delta", "chat_id": "t-stream", "text": "I will inspect first."},
        {"event": "stream_end", "chat_id": "t-stream"},
        {
            "event": "message",
            "chat_id": "t-stream",
            "text": 'python_exec({"code":"ls"})',
            "kind": "tool_hint",
        },
        {
            "event": "stream_end",
            "chat_id": "t-stream",
            "text": "Done. Open index.html to play.",
        },
    ])

    assert len(msgs) == 3
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == ""
    assert msgs[0]["reasoning"] == "I will inspect first."
    assert "isStreaming" not in msgs[0]
    assert msgs[1]["kind"] == "trace"
    assert msgs[1]["traces"] == ['python_exec({"code":"ls"})']
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content"] == "Done. Open index.html to play."


def test_replay_tool_events_dedupes_finish_after_start() -> None:
    msgs = replay_transcript_to_ui_messages([
        {
            "event": "message",
            "chat_id": "t-tool",
            "text": 'python_exec({"code":"ls"})',
            "kind": "tool_hint",
            "tool_events": [
                {
                    "phase": "start",
                    "call_id": "call-exec",
                    "name": "python_exec",
                    "arguments": {"code": "ls"},
                },
            ],
        },
        {
            "event": "message",
            "chat_id": "t-tool",
            "text": "",
            "kind": "progress",
            "tool_events": [
                {
                    "phase": "end",
                    "call_id": "call-exec",
                    "name": "python_exec",
                    "arguments": {"code": "ls"},
                    "result": "ok",
                },
                {
                    "phase": "end",
                    "call_id": "call-read",
                    "name": "read_file",
                    "arguments": {"path": "notes.md"},
                    "result": "done",
                },
            ],
        },
    ])

    assert len(msgs) == 1
    assert msgs[0]["traces"] == [
        'python_exec({"code": "ls"})',
        'read_file({"path": "notes.md"})',
    ]
    assert msgs[0]["toolEvents"][0]["phase"] == "end"
    assert msgs[0]["toolEvents"][0]["call_id"] == "call-exec"


def test_replay_file_edit_progress_merges_after_interleaved_activity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-file-progress"
    for ev in (
        {"event": "user", "chat_id": "t-file-progress", "text": "edit"},
        {
            "event": "message",
            "chat_id": "t-file-progress",
            "text": 'write_file({"path":"foo.txt"})',
            "kind": "tool_hint",
        },
        {
            "event": "file_edit",
            "chat_id": "t-file-progress",
            "edits": [
                {
                    "version": 1,
                    "call_id": "call-write",
                    "tool": "write_file",
                    "path": "foo.txt",
                    "phase": "start",
                    "added": 12,
                    "deleted": 0,
                    "approximate": True,
                    "status": "editing",
                },
            ],
        },
        {
            "event": "message",
            "chat_id": "t-file-progress",
            "text": "still working",
            "kind": "progress",
        },
        {
            "event": "file_edit",
            "chat_id": "t-file-progress",
            "edits": [
                {
                    "version": 1,
                    "call_id": "call-write",
                    "tool": "write_file",
                    "path": "foo.txt",
                    "phase": "end",
                    "added": 30,
                    "deleted": 0,
                    "approximate": False,
                    "status": "done",
                },
            ],
        },
    ):
        append_transcript_object(key, ev)

    msgs = replay_transcript_to_ui_messages(read_transcript_lines(key))
    file_edit_messages = [msg for msg in msgs if msg.get("fileEdits")]

    assert len(file_edit_messages) == 1
    assert file_edit_messages[0]["fileEdits"] == [
        {
            "version": 1,
            "call_id": "call-write",
            "tool": "write_file",
            "path": "foo.txt",
            "phase": "end",
            "added": 30,
            "deleted": 0,
            "approximate": False,
            "status": "done",
        },
    ]


def test_replay_file_edit_pending_placeholder_upgrades_to_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-file-pending"
    for ev in (
        {"event": "user", "chat_id": "t-file-pending", "text": "write"},
        {
            "event": "file_edit",
            "chat_id": "t-file-pending",
            "edits": [
                {
                    "version": 1,
                    "call_id": "call-write",
                    "tool": "write_file",
                    "path": "",
                    "phase": "start",
                    "added": 1,
                    "deleted": 0,
                    "approximate": True,
                    "status": "editing",
                    "pending": True,
                },
            ],
        },
        {
            "event": "file_edit",
            "chat_id": "t-file-pending",
            "edits": [
                {
                    "version": 1,
                    "call_id": "call-write",
                    "tool": "write_file",
                    "path": "foo.txt",
                    "phase": "start",
                    "added": 12,
                    "deleted": 0,
                    "approximate": True,
                    "status": "editing",
                },
            ],
        },
    ):
        append_transcript_object(key, ev)

    msgs = replay_transcript_to_ui_messages(read_transcript_lines(key))
    file_edit_messages = [msg for msg in msgs if msg.get("fileEdits")]

    assert len(file_edit_messages) == 1
    assert file_edit_messages[0]["fileEdits"] == [
        {
            "version": 1,
            "call_id": "call-write",
            "tool": "write_file",
            "path": "foo.txt",
            "phase": "start",
            "added": 12,
            "deleted": 0,
            "approximate": True,
            "status": "editing",
        },
    ]


def test_replay_keeps_new_file_edit_after_reasoning_in_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t-file-order"
    for ev in (
        {"event": "user", "chat_id": "t-file-order", "text": "edit"},
        {
            "event": "file_edit",
            "chat_id": "t-file-order",
            "edits": [
                {
                    "version": 1,
                    "call_id": "call-one",
                    "tool": "write_file",
                    "path": "one.txt",
                    "phase": "start",
                    "added": 10,
                    "deleted": 0,
                    "approximate": True,
                    "status": "editing",
                },
            ],
        },
        {"event": "reasoning_delta", "chat_id": "t-file-order", "text": "Check next."},
        {"event": "reasoning_end", "chat_id": "t-file-order"},
        {
            "event": "file_edit",
            "chat_id": "t-file-order",
            "edits": [
                {
                    "version": 1,
                    "call_id": "call-two",
                    "tool": "write_file",
                    "path": "two.txt",
                    "phase": "start",
                    "added": 20,
                    "deleted": 0,
                    "approximate": True,
                    "status": "editing",
                },
            ],
        },
    ):
        append_transcript_object(key, ev)

    msgs = replay_transcript_to_ui_messages(read_transcript_lines(key))

    assert [msg.get("fileEdits", [{}])[0].get("path") if msg.get("fileEdits") else msg.get("reasoning") for msg in msgs[1:]] == [
        "one.txt",
        "Check next.",
        "two.txt",
    ]
    file_edit_segments = [
        msg.get("activitySegmentId")
        for msg in msgs
        if msg.get("fileEdits")
    ]
    assert len(file_edit_segments) == 2
    assert file_edit_segments[0] != file_edit_segments[1]


def test_build_response_schema(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:t3"
    append_transcript_object(key, {"event": "user", "chat_id": "t3", "text": "x"})
    out = build_webui_thread_response(key, augment_user_media=None)
    assert out is not None
    assert out["schemaVersion"] == WEBUI_TRANSCRIPT_SCHEMA_VERSION
    assert out["sessionKey"] == key
    assert len(out["messages"]) == 1


# ── Il confine di /new e' un'interruzione di pagina ────────────────────────────
#
# Da 0.9.x `/new` non lascia piu' la conversazione vecchia a schermo: il
# separatore che il comando scrive nel transcript e' anche il punto in cui la
# cronologia visibile comincia. Non e' una cancellazione — questi test tengono
# fermi entrambi i versi: la prima pagina si ferma li', e chi scorre in su
# ritrova quel che c'era prima.


def _append_session_boundary(key: str, chat_id: str) -> None:
    """La coppia di righe che `/new` lascia: il comando e il suo confine.

    Senza `turn_end` di proposito: e' come le scrive la produzione — un comando
    non apre un turno dell'agente, e ``_split_transcript_turns`` spezza solo su
    quello.
    """
    append_transcript_object(key, {"event": "user", "chat_id": chat_id, "text": "/new"})
    append_transcript_object(
        key,
        {
            "event": "message",
            "chat_id": chat_id,
            "text": "New session started.",
            "session_boundary": True,
        },
    )


def test_first_page_starts_at_the_last_session_boundary(tmp_path, monkeypatch) -> None:
    key = "websocket:boundary"
    _write_segmented_turns(tmp_path, monkeypatch, key, "boundary", 4)
    _append_session_boundary(key, "boundary")
    _append_numbered_turn(key, "boundary", 5)

    page = build_webui_thread_response(key, limit=40)

    assert page is not None
    contents = _message_contents(page)
    # Il confine e quel che viene dopo, e nulla di prima: il limite era 40, cioe'
    # abbondante per tutto il transcript — chi ha chiuso la pagina e' il confine.
    assert "New session started." in contents
    assert _numbered_turn_texts(5, 5)[0] in contents
    assert _numbered_turn_texts(1, 4)[0] not in contents
    # E si dichiara: senza questo lo scroll in su non partirebbe nemmeno.
    assert page["page"]["has_more_before"] is True
    assert page["page"]["before_cursor"]


def test_scrolling_past_the_boundary_returns_the_previous_session(
    tmp_path,
    monkeypatch,
) -> None:
    key = "websocket:boundary-back"
    _write_segmented_turns(tmp_path, monkeypatch, key, "boundary-back", 4)
    _append_session_boundary(key, "boundary-back")
    _append_numbered_turn(key, "boundary-back", 5)

    first = build_webui_thread_response(key, limit=40)
    assert first is not None
    older = build_webui_thread_response(key, limit=40, before=first["page"]["before_cursor"])

    assert older is not None
    # Tutta la conversazione precedente, in una pagina: nessuna riga e' andata
    # perduta, era solo sotto la piega.
    assert _message_contents(older) == _numbered_turn_texts(1, 4)
    assert older["page"]["has_more_before"] is False


def test_two_boundaries_in_a_row_are_one_page_break(tmp_path, monkeypatch) -> None:
    """Due `/new` di fila non fanno girare a vuoto la paginazione.

    Senza un turno in mezzo le quattro righe stanno nello **stesso** turno
    (``_split_transcript_turns`` spezza solo su ``turn_end``), quindi le
    interruzioni sono una, non due: la pagina dopo e' gia' la conversazione
    precedente. Il punto del test e' che si scende — ogni pagina consuma almeno
    un turno e ``before`` esclude quello da cui si e' partiti, quindi non esiste
    la richiesta che ripresenta se stessa.
    """
    key = "websocket:boundary-twice"
    _write_segmented_turns(tmp_path, monkeypatch, key, "boundary-twice", 2)
    _append_session_boundary(key, "boundary-twice")
    _append_session_boundary(key, "boundary-twice")

    first = build_webui_thread_response(key, limit=40)
    assert first is not None
    cursor = first["page"]["before_cursor"]
    assert cursor
    assert _numbered_turn_texts(1, 2)[0] not in _message_contents(first)

    second = build_webui_thread_response(key, limit=40, before=cursor)
    assert second is not None
    assert _message_contents(second) == _numbered_turn_texts(1, 2)
    assert second["page"]["has_more_before"] is False


_SUBAGENT_ANNOUNCE = (
    "[Subagent 'backup latest pibox' completed successfully]\n\n"
    "Task: Fai una singola chiamata al tool MCP `latest` sull'hub…\n\n"
    "Result:\nlocale ok, remoto ok\n\n"
    "Summarize this naturally for the user."
)


def test_cron_turn_does_not_backfill_a_subagent_announce_as_a_user_bubble(
    tmp_path,
    monkeypatch,
) -> None:
    """Il rientro di un subagent non è una bolla dell'utente.

    Misurato sul device il 05/09/2026: il turno del cron ``backup-daily-recap``
    non ha evento ``user`` nel transcript di display (il trigger non ci passa),
    quindi il backfill va a cercarne uno in sessione. Scartava il trigger per
    ``_cron_turn`` e trovava subito dopo l'annuncio del subagent — iniettato a
    metà turno con ``role: "user"`` e nessun marcatore — e lo mostrava in chat
    con dentro il prompt integrale del subagent.
    """
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:cron-announce"
    append_transcript_object(
        key,
        {"event": "message", "chat_id": "cron-announce", "text": "buongiorno boss, backup ok"},
    )
    append_transcript_object(key, {"event": "turn_end", "chat_id": "cron-announce"})

    out = build_webui_thread_response(
        key,
        session_messages=[
            {
                "role": "user",
                "content": "Scheduled cron job triggered: backup-daily-recap",
                "_cron_turn": True,
            },
            {"role": "assistant", "content": "delego la lettura a un subagent"},
            {
                "role": "user",
                "content": _SUBAGENT_ANNOUNCE,
                "injected_event": "subagent_result",
                "subagent_task_id": "ff0941f9",
            },
            {"role": "assistant", "content": "buongiorno boss, backup ok"},
        ],
    )

    assert out is not None
    assert [(m["role"], m["content"]) for m in out["messages"]] == [
        ("assistant", "buongiorno boss, backup ok"),
    ]


def test_cron_turn_still_backfills_a_real_user_message(tmp_path, monkeypatch) -> None:
    """Controllo positivo: senza il marcatore il backfill fa ancora il suo lavoro.

    Tiene il test qui sopra onesto — deve fallire perché l'annuncio è *marcato*,
    non perché il backfill ha smesso di funzionare.
    """
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    key = "websocket:real-user"
    append_transcript_object(
        key,
        {"event": "message", "chat_id": "real-user", "text": "buongiorno boss, backup ok"},
    )
    append_transcript_object(key, {"event": "turn_end", "chat_id": "real-user"})

    out = build_webui_thread_response(
        key,
        session_messages=[
            {"role": "user", "content": "com'è andato il backup?"},
            {"role": "assistant", "content": "buongiorno boss, backup ok"},
        ],
    )

    assert out is not None
    assert [(m["role"], m["content"]) for m in out["messages"]] == [
        ("user", "com'è andato il backup?"),
        ("assistant", "buongiorno boss, backup ok"),
    ]
