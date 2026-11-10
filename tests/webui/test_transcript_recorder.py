"""Test unitari per ``jenny.webui.transcript_recorder``.

`test_webui_transcript.py` copre gia il replay e lo storage ad alto livello;
qui si copre il recorder (`WebUITranscriptRecorder`) che non ha ancora
copertura diretta: normalizzazione del turn id, annotazione fasi/sequenze,
e robustezza a input malformati.
"""

from __future__ import annotations

from jenny.bus.events import WEBUI_TURN_METADATA_KEY
from jenny.webui.transcript_recorder import (
    WebUITranscriptRecorder,
    _build_user_transcript_event,
    _normalize_webui_turn_id,
)


def _configure_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)


# --- _normalize_webui_turn_id ------------------------------------------------


def test_normalize_turn_id_accepts_valid_string() -> None:
    assert _normalize_webui_turn_id("turn-1.a:b_c") == "turn-1.a:b_c"


def test_normalize_turn_id_rejects_invalid_characters() -> None:
    # Spaces non sono ammessi dal pattern: deve ricadere su un uuid generato.
    generated = _normalize_webui_turn_id("has spaces")
    assert generated != "has spaces"
    assert len(generated) == 36  # forma uuid4


def test_normalize_turn_id_rejects_too_long_string() -> None:
    too_long = "a" * 129
    generated = _normalize_webui_turn_id(too_long)
    assert generated != too_long


def test_normalize_turn_id_falls_back_for_non_string() -> None:
    assert len(_normalize_webui_turn_id(123)) == 36
    assert len(_normalize_webui_turn_id(None)) == 36


def test_normalize_turn_id_strips_whitespace() -> None:
    assert _normalize_webui_turn_id("  abc  ") == "abc"


# --- _build_user_transcript_event --------------------------------------------


def test_build_user_transcript_event_none_when_empty() -> None:
    assert _build_user_transcript_event("chat-1", "") is None
    assert _build_user_transcript_event("chat-1", "   ") is not None  # spazi soli contano come testo


def test_build_user_transcript_event_text_only() -> None:
    event = _build_user_transcript_event("chat-1", "hello")
    assert event == {"event": "user", "chat_id": "chat-1", "text": "hello"}


def test_build_user_transcript_event_with_media_and_no_text() -> None:
    event = _build_user_transcript_event("chat-1", "", media_paths=["/a.png", None, "/b.png"])
    assert event is not None
    assert event["media_paths"] == ["/a.png", "/b.png"]
    assert event["text"] == ""


def test_build_user_transcript_event_casts_media_paths_to_str() -> None:
    event = _build_user_transcript_event("chat-1", "hi", media_paths=[123])
    assert event["media_paths"] == ["123"]


# --- WebUITranscriptRecorder.client_turn_metadata -----------------------------


def test_client_turn_metadata_normalizes_value() -> None:
    recorder = WebUITranscriptRecorder(log=_NullLog())
    meta = recorder.client_turn_metadata("turn-abc")
    assert meta == {WEBUI_TURN_METADATA_KEY: "turn-abc"}


# --- prepare_event -------------------------------------------------------------


class _NullLog:
    """Logger fittizio che registra i warning senza stampare nulla."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, tuple]] = []

    def warning(self, msg: str, *args: object) -> None:
        self.warnings.append((msg, args))


def test_prepare_event_without_phase_leaves_event_untouched() -> None:
    recorder = WebUITranscriptRecorder(log=_NullLog())
    event: dict = {"event": "message", "text": "hi"}
    recorder.prepare_event("chat-1", event, metadata={WEBUI_TURN_METADATA_KEY: "t1"})
    assert "turn_id" not in event
    assert "turn_phase" not in event


def test_prepare_event_with_phase_annotates_turn_fields() -> None:
    recorder = WebUITranscriptRecorder(log=_NullLog())
    event: dict = {"event": "message"}
    recorder.prepare_event(
        "chat-1",
        event,
        metadata={WEBUI_TURN_METADATA_KEY: "t1"},
        phase="user",
    )
    assert event["turn_id"] == "t1"
    assert event["turn_phase"] == "user"
    assert event["turn_seq"] == 1


def test_prepare_event_with_phase_but_missing_turn_id_skips_annotation() -> None:
    recorder = WebUITranscriptRecorder(log=_NullLog())
    event: dict = {"event": "message"}
    recorder.prepare_event("chat-1", event, metadata=None, phase="user")
    assert "turn_id" not in event


# --- turn sequencing (_next_turn_seq / _annotate_turn) ------------------------


def test_turn_sequence_increments_and_resets_on_complete() -> None:
    recorder = WebUITranscriptRecorder(log=_NullLog())
    metadata = {WEBUI_TURN_METADATA_KEY: "t1"}

    first: dict = {}
    recorder.prepare_event("chat-1", first, metadata=metadata, phase="activity")
    second: dict = {}
    recorder.prepare_event("chat-1", second, metadata=metadata, phase="activity")
    third: dict = {}
    recorder.prepare_event("chat-1", third, metadata=metadata, phase="complete")

    assert [first["turn_seq"], second["turn_seq"], third["turn_seq"]] == [1, 2, 3]

    # Dopo "complete" il contatore per (chat, turn) riparte da 1.
    fourth: dict = {}
    recorder.prepare_event("chat-1", fourth, metadata=metadata, phase="activity")
    assert fourth["turn_seq"] == 1


def test_turn_sequence_is_independent_per_chat_id() -> None:
    recorder = WebUITranscriptRecorder(log=_NullLog())
    metadata = {WEBUI_TURN_METADATA_KEY: "t1"}

    ev_a: dict = {}
    recorder.prepare_event("chat-a", ev_a, metadata=metadata, phase="activity")
    ev_b: dict = {}
    recorder.prepare_event("chat-b", ev_b, metadata=metadata, phase="activity")

    assert ev_a["turn_seq"] == 1
    assert ev_b["turn_seq"] == 1


# --- prepare_and_append --------------------------------------------------------


def test_prepare_and_append_persists_event_with_overrides(tmp_path, monkeypatch) -> None:
    _configure_workspace(tmp_path, monkeypatch)
    recorder = WebUITranscriptRecorder(log=_NullLog())
    recorder.prepare_and_append(
        "chat-1",
        {"event": "message", "text": "hi"},
        metadata={WEBUI_TURN_METADATA_KEY: "t1"},
        phase="answer",
        transcript_overrides={"latency_ms": 42},
    )

    from jenny.webui.transcript_store import read_transcript_lines

    lines = read_transcript_lines("websocket:chat-1")
    assert len(lines) == 1
    assert lines[0]["text"] == "hi"
    assert lines[0]["turn_phase"] == "answer"
    assert lines[0]["latency_ms"] == 42


# --- append_user_message -------------------------------------------------------


def test_append_user_message_skips_bare_stop_command(tmp_path, monkeypatch) -> None:
    _configure_workspace(tmp_path, monkeypatch)
    recorder = WebUITranscriptRecorder(log=_NullLog())
    recorder.append_user_message("chat-1", "/stop", metadata={})

    from jenny.webui.transcript_store import read_transcript_lines

    assert read_transcript_lines("websocket:chat-1") == []


def test_append_user_message_skips_bare_new_command(tmp_path, monkeypatch) -> None:
    """Stessa ragione di `/stop`, piu' una che e' solo di `/new`.

    Il confine che il comando lascia e' il pavimento della cronologia visibile,
    ma la riga utente sta nello **stesso turno** del confine — i turni si
    spezzano su `turn_end`, e un comando non ne apre uno — quindi non finisce
    sotto quel pavimento: senza questo, la chat appena azzerata si riapriva con
    un `/new` appeso in cima allo schermo pulito.
    """
    _configure_workspace(tmp_path, monkeypatch)
    recorder = WebUITranscriptRecorder(log=_NullLog())
    recorder.append_user_message("chat-1", "/new", metadata={})

    from jenny.webui.transcript_store import read_transcript_lines

    assert read_transcript_lines("websocket:chat-1") == []


def test_append_user_message_keeps_the_commands_that_are_decisions(tmp_path, monkeypatch) -> None:
    """`/model fast` non e' rumore: rileggere cosa si e' scelto, e quando, serve."""
    _configure_workspace(tmp_path, monkeypatch)
    recorder = WebUITranscriptRecorder(log=_NullLog())
    recorder.append_user_message("chat-1", "/model fast", metadata={})

    from jenny.webui.transcript_store import read_transcript_lines

    lines = read_transcript_lines("websocket:chat-1")
    assert len(lines) == 1
    assert lines[0]["text"] == "/model fast"


def test_append_user_message_persists_stop_when_media_attached(tmp_path, monkeypatch) -> None:
    _configure_workspace(tmp_path, monkeypatch)
    recorder = WebUITranscriptRecorder(log=_NullLog())
    recorder.append_user_message("chat-1", "/stop", metadata={}, media_paths=["/a.png"])

    from jenny.webui.transcript_store import read_transcript_lines

    lines = read_transcript_lines("websocket:chat-1")
    assert len(lines) == 1
    assert lines[0]["media_paths"] == ["/a.png"]


def test_append_user_message_skips_empty_payload(tmp_path, monkeypatch) -> None:
    _configure_workspace(tmp_path, monkeypatch)
    recorder = WebUITranscriptRecorder(log=_NullLog())
    recorder.append_user_message("chat-1", "", metadata={})

    from jenny.webui.transcript_store import read_transcript_lines

    assert read_transcript_lines("websocket:chat-1") == []


def test_append_user_message_annotates_turn_when_metadata_present(tmp_path, monkeypatch) -> None:
    _configure_workspace(tmp_path, monkeypatch)
    recorder = WebUITranscriptRecorder(log=_NullLog())
    recorder.append_user_message(
        "chat-1",
        "hello",
        metadata={WEBUI_TURN_METADATA_KEY: "t9"},
    )

    from jenny.webui.transcript_store import read_transcript_lines

    lines = read_transcript_lines("websocket:chat-1")
    assert lines[0]["turn_id"] == "t9"
    assert lines[0]["turn_phase"] == "user"
    assert lines[0]["turn_seq"] == 1


# --- append: robustezza a input malformati -------------------------------------


def test_append_swallows_non_serializable_payload(tmp_path, monkeypatch) -> None:
    """Un evento non serializzabile in JSON non deve propagare l'eccezione."""
    _configure_workspace(tmp_path, monkeypatch)
    log = _NullLog()
    recorder = WebUITranscriptRecorder(log=log)

    class _Unserializable:
        pass

    recorder.append("chat-1", {"event": "message", "bad": _Unserializable()})

    assert len(log.warnings) == 1

    from jenny.webui.transcript_store import read_transcript_lines

    assert read_transcript_lines("websocket:chat-1") == []


def test_append_persists_a_deep_copy_not_a_reference(tmp_path, monkeypatch) -> None:
    _configure_workspace(tmp_path, monkeypatch)
    recorder = WebUITranscriptRecorder(log=_NullLog())
    event = {"event": "message", "text": "original"}
    recorder.append("chat-1", event)
    event["text"] = "mutated-after-append"

    from jenny.webui.transcript_store import read_transcript_lines

    lines = read_transcript_lines("websocket:chat-1")
    assert lines[0]["text"] == "original"
