"""L'ora sotto ogni messaggio: da dove la prende la cronologia della WebUI.

Dal 06/10/2026 sotto ogni messaggio c'e' l'ora in cui e' stato scritto (solo
l'ora se e' di oggi, data e ora se e' di prima). La cronologia della WebUI non
viene dalla sessione ma dal transcript, e il transcript un'ora non l'aveva:
il ``createdAt`` dei messaggi il replay lo inventa al momento della lettura.

Qui si tiene ferma la catena: ogni riga scritta porta ``at`` (ms epoch), il
replay ne fa ``sentAt`` — per una risposta, quello della chiusura del turno — e
le cronologie scritte prima la prendono dalla sessione, dove ogni messaggio ha
il suo ``timestamp`` da sempre.
"""

from __future__ import annotations

import time
from datetime import datetime

from jenny.webui.transcript import (
    _build_synthetic_transcript,
    _inject_missing_user_events_from_session,
)
from jenny.webui.transcript_recorder import WebUITranscriptRecorder
from jenny.webui.transcript_replay import replay_transcript_to_ui_messages
from jenny.webui.transcript_store import read_transcript_lines

USER_AT = 1_791_200_000_000
END_AT = USER_AT + 4_200


def _turn(*, user_at: int | None = USER_AT, end_at: int | None = END_AT) -> list[dict]:
    def stamped(rec: dict, at: int | None) -> dict:
        return {**rec, "at": at} if at is not None else rec

    return [
        stamped({"event": "user", "chat_id": "c", "text": "ciao",
                 "turn_id": "t1", "turn_phase": "user"}, user_at),
        stamped({"event": "delta", "chat_id": "c", "text": "Ciao!",
                 "turn_id": "t1", "turn_phase": "answer"}, user_at and user_at + 900),
        stamped({"event": "stream_end", "chat_id": "c",
                 "turn_id": "t1", "turn_phase": "answer"}, user_at and user_at + 1_000),
        stamped({"event": "turn_end", "chat_id": "c", "latency_ms": 4200,
                 "turn_id": "t1", "turn_phase": "complete"}, end_at),
    ]


def _by_role(messages: list[dict]) -> dict[str, dict]:
    return {m["role"]: m for m in messages if m.get("kind") != "trace"}


def test_the_recorder_stamps_every_line(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    rec = WebUITranscriptRecorder()
    before = int(time.time() * 1000)
    rec.append_user_message("c", "ciao", metadata={})
    rec.append("c", {"event": "turn_end", "chat_id": "c"})
    lines = read_transcript_lines("websocket:c")
    assert len(lines) == 2
    for line in lines:
        assert before <= line["at"] <= int(time.time() * 1000)


def test_a_line_that_already_has_its_time_keeps_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    WebUITranscriptRecorder().append("c", {"event": "turn_end", "chat_id": "c", "at": 5})
    assert read_transcript_lines("websocket:c")[0]["at"] == 5


def test_replay_gives_the_user_its_line_and_the_answer_the_end_of_its_turn() -> None:
    msgs = _by_role(replay_transcript_to_ui_messages(_turn()))
    assert msgs["user"]["sentAt"] == USER_AT
    assert msgs["assistant"]["sentAt"] == END_AT


def test_a_turn_without_its_end_keeps_the_time_of_its_text() -> None:
    lines = _turn()[:-1]
    msgs = _by_role(replay_transcript_to_ui_messages(lines))
    assert msgs["assistant"]["sentAt"] == USER_AT + 900


def test_an_old_transcript_without_times_gives_no_time() -> None:
    msgs = replay_transcript_to_ui_messages(_turn(user_at=None, end_at=None))
    assert msgs and all("sentAt" not in m for m in msgs)


def _session(user_ts: str, answer_ts: str) -> list[dict]:
    return [
        {"role": "user", "content": "ciao", "timestamp": user_ts},
        {"role": "assistant", "content": "Ciao!", "timestamp": answer_ts},
    ]


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def test_an_old_transcript_takes_the_times_from_the_session() -> None:
    session = _session("2026-10-05T18:04:10.5", "2026-10-05T18:04:14.1")
    lines = _inject_missing_user_events_from_session(
        "websocket:c", _turn(user_at=None, end_at=None), session,
    )
    msgs = _by_role(replay_transcript_to_ui_messages(lines))
    assert msgs["user"]["sentAt"] == _ms("2026-10-05T18:04:10.5")
    assert msgs["assistant"]["sentAt"] == _ms("2026-10-05T18:04:14.1")


def test_an_old_answer_only_transcript_gets_its_user_row_with_its_time() -> None:
    session = _session("2026-10-05T18:04:10", "2026-10-05T18:04:14")
    answer_only = [rec for rec in _turn(user_at=None, end_at=None) if rec["event"] != "user"]
    lines = _inject_missing_user_events_from_session("websocket:c", answer_only, session)
    msgs = _by_role(replay_transcript_to_ui_messages(lines))
    assert msgs["user"]["content"] == "ciao"
    assert msgs["user"]["sentAt"] == _ms("2026-10-05T18:04:10")
    assert msgs["assistant"]["sentAt"] == _ms("2026-10-05T18:04:14")


def test_the_session_does_not_overwrite_a_time_the_transcript_has() -> None:
    session = _session("2026-10-05T18:04:10", "2026-10-05T18:04:14")
    lines = _inject_missing_user_events_from_session("websocket:c", _turn(), session)
    msgs = _by_role(replay_transcript_to_ui_messages(lines))
    assert msgs["user"]["sentAt"] == USER_AT
    assert msgs["assistant"]["sentAt"] == END_AT


def test_a_synthetic_transcript_carries_the_session_times() -> None:
    session = _session("2026-10-05T18:04:10", "2026-10-05T18:04:14")
    lines = _build_synthetic_transcript("websocket:c", session)
    msgs = _by_role(replay_transcript_to_ui_messages(lines))
    assert msgs["user"]["sentAt"] == _ms("2026-10-05T18:04:10")
    assert msgs["assistant"]["sentAt"] == _ms("2026-10-05T18:04:14")


def test_a_session_row_without_a_valid_timestamp_gives_no_time() -> None:
    session = [
        {"role": "user", "content": "ciao", "timestamp": "ieri"},
        {"role": "assistant", "content": "Ciao!"},
    ]
    lines = _build_synthetic_transcript("websocket:c", session)
    msgs = replay_transcript_to_ui_messages(lines)
    assert msgs and all("sentAt" not in m for m in msgs)
