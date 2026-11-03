"""I pezzetti dello streaming si fondono a turno chiuso, e il replay non cambia.

Sul telefono di prova (03/11/2026) i ``delta`` e i ``reasoning_delta``, uno per
chunk del provider, erano il 95% delle righe dei verbali. Fusi a turno chiuso,
il replay era identico su tutti i 2056 turni veri: qui lo stesso confronto su
turni costruiti apposta, più la passata d'avvio sui turni di prima.
"""

from __future__ import annotations

from typing import Any

import pytest

from jenny.webui import transcript_store as ts
from jenny.webui.transcript_replay import replay_transcript_to_ui_messages


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    return tmp_path


KEY = "websocket:default"


def _turn(turn: str, *, answer: list[str], reasoning: list[str] = (), user: str = "ciao",
          extra: list[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """I frame di un turno come li scrive la WebUI: un pezzetto per chunk."""
    frames: list[dict[str, Any]] = [
        {"event": "user", "chat_id": "default", "text": user, "turn_id": turn,
         "turn_phase": "user", "turn_seq": 1, "at": 1000},
    ]
    seq = 2
    for piece in reasoning:
        frames.append({"event": "reasoning_delta", "chat_id": "default", "text": piece,
                       "turn_id": turn, "turn_phase": "reasoning", "turn_seq": seq,
                       "at": 1000 + seq})
        seq += 1
    if reasoning:
        frames.append({"event": "reasoning_end", "chat_id": "default", "turn_id": turn,
                       "turn_seq": seq})
        seq += 1
    frames.extend(extra)
    for piece in answer:
        frames.append({"event": "delta", "chat_id": "default", "text": piece,
                       "stream_id": f"{turn}:0", "turn_id": turn, "turn_phase": "answer",
                       "turn_seq": seq, "at": 1000 + seq})
        seq += 1
    frames.append({"event": "stream_end", "chat_id": "default", "stream_id": f"{turn}:0",
                   "turn_id": turn, "turn_phase": "answer", "turn_seq": seq})
    frames.append({"event": "turn_end", "chat_id": "default", "turn_id": turn,
                   "turn_seq": seq + 1})
    return frames


def _without_ids(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # ``id`` e ``createdAt`` cambiano a ogni replay anche oggi (un uuid, l'ora).
    return [{k: v for k, v in m.items() if k not in ("id", "createdAt")} for m in messages]


def _rows(key: str = KEY) -> list[dict[str, Any]]:
    return ts.read_transcript_lines(key)


def test_consecutive_pieces_of_one_stretch_fuse_and_keep_the_last_fields() -> None:
    frames = [
        (0, {"event": "delta", "text": "Ci", "stream_id": "s", "turn_seq": 1, "at": 1}),
        (1, {"event": "delta", "text": "ao", "stream_id": "s", "turn_seq": 2, "at": 2}),
        (2, {"event": "delta", "text": "!", "stream_id": "t", "turn_seq": 3, "at": 3}),
        (3, {"event": "reasoning_delta", "text": "a", "turn_seq": 4}),
        (4, {"event": "message", "kind": "progress", "text": "x"}),
        (5, {"event": "reasoning_delta", "text": "b", "turn_seq": 6}),
        (6, {"event": "reasoning_delta", "text": "c", "turn_seq": 7}),
    ]
    fused = ts.fuse_frames(frames)
    assert fused == [
        (0, {"event": "delta", "text": "Ciao", "stream_id": "s", "turn_seq": 2, "at": 2}, [1]),
        (5, {"event": "reasoning_delta", "text": "bc", "turn_seq": 7}, [6]),
    ]


def test_the_turn_is_compacted_when_it_closes_not_before(data_dir) -> None:
    frames = _turn("t1", answer=["Il ", "vaso ", "è ", "da 20 cm."], reasoning=["pen", "so"])
    for frame in frames[:-1]:
        ts.append_transcript_object(KEY, frame)
    assert len(_rows()) == len(frames) - 1, "a turno aperto i pezzetti restano"
    ts.append_transcript_object(KEY, frames[-1])
    rows = _rows()
    assert [r["event"] for r in rows] == [
        "user", "reasoning_delta", "reasoning_end", "delta", "stream_end", "turn_end"]
    assert rows[3]["text"] == "Il vaso è da 20 cm."


@pytest.mark.parametrize("extra", [
    [],
    [{"event": "message", "chat_id": "default", "kind": "progress", "text": "",
      "tool_hint": True, "turn_id": "t1", "turn_phase": "activity", "turn_seq": 9}],
    [{"event": "file_edit", "chat_id": "default", "turn_id": "t1", "turn_seq": 9,
      "edits": [{"path": "wiki/a.md", "op": "write"}]}],
])
def test_the_replay_is_the_same_before_and_after(data_dir, monkeypatch, extra) -> None:
    frames = _turn("t1", answer=["Prima ", "parte, ", "seconda."], reasoning=["a", "b", "c"],
                   extra=extra)
    before = replay_transcript_to_ui_messages(frames)
    for frame in frames:
        ts.append_transcript_object(KEY, frame)
    assert len(_rows()) < len(frames)
    assert _without_ids(replay_transcript_to_ui_messages(_rows())) == _without_ids(before)


def _legacy(turns: int, key: str = KEY) -> None:
    """Turni scritti senza fusione: come quelli di prima di questa versione."""
    real = ts.compact_turn
    ts.compact_turn = lambda *a: 0  # type: ignore[assignment]
    try:
        for n in range(turns):
            for frame in _turn(f"t{n}", answer=["uno ", "due ", "tre"]):
                ts.append_transcript_object(key, frame)
    finally:
        ts.compact_turn = real  # type: ignore[assignment]
    with ts._db().write() as conn:
        conn.execute("DELETE FROM transcript_compacted")


def test_the_startup_pass_compacts_the_old_turns_once(data_dir) -> None:
    _legacy(5)
    before = replay_transcript_to_ui_messages(_rows())
    assert ts.compact_closed_turns(batch=2) == 5 * 2
    assert _without_ids(replay_transcript_to_ui_messages(_rows())) == _without_ids(before)
    assert ts.compact_closed_turns() == 0
    row = ts._db().read("SELECT through_turn FROM transcript_compacted WHERE key = ?", (KEY,))
    assert row[0]["through_turn"] == 4


def test_a_new_turn_does_not_mark_the_old_ones_as_done(data_dir) -> None:
    """Il primo turno dopo l'aggiornamento non sposta il segnalibro oltre i turni
    vecchi: la passata d'avvio deve ancora trovarli."""
    _legacy(3)
    for frame in _turn("nuovo", answer=["a", "b"]):
        ts.append_transcript_object(KEY, frame)
    assert not ts._db().read("SELECT 1 FROM transcript_compacted WHERE key = ?", (KEY,))
    assert ts.compact_closed_turns() == 3 * 2


def test_the_bookmark_follows_a_rename_and_a_delete(data_dir) -> None:
    for frame in _turn("t0", answer=["a", "b"]):
        ts.append_transcript_object("websocket:project:a", frame)
    assert ts.rename_webui_transcript("websocket:project:a", "websocket:project:b")
    keys = [r["key"] for r in ts._db().read("SELECT key FROM transcript_compacted")]
    assert keys == ["websocket:project:b"]
    assert ts.delete_webui_transcript("websocket:project:b")
    assert not ts._db().read("SELECT 1 FROM transcript_compacted")
