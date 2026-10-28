"""Test unitari mirati per ``jenny.webui.transcript_replay``.

`test_webui_transcript.py` copre gia estesamente il fold (delta, reasoning,
tool_hint/progress, file_edit, media, turn_end) tramite `replay_transcript_to_ui_messages`;
qui si aggiungono i rami rimasti scoperti: eventi sconosciuti/malformati (chiavi
mancanti, tipi inattesi) e la soppressione post-media fino a `turn_end`.
"""

from __future__ import annotations

from jenny.webui.transcript_replay import replay_transcript_to_ui_messages


def test_unknown_event_type_is_ignored() -> None:
    lines = [{"event": "something_unexpected", "text": "noise"}]
    assert replay_transcript_to_ui_messages(lines) == []


def test_record_missing_event_key_is_ignored() -> None:
    lines = [{"chat_id": "c1", "text": "no event field at all"}]
    assert replay_transcript_to_ui_messages(lines) == []


def test_user_event_with_non_string_text_defaults_to_empty_content() -> None:
    lines = [{"event": "user", "chat_id": "c1", "text": 12345}]
    messages = replay_transcript_to_ui_messages(lines)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == ""


def test_user_event_with_non_list_media_paths_ignored() -> None:
    lines = [{"event": "user", "chat_id": "c1", "text": "hi", "media_paths": "not-a-list"}]
    messages = replay_transcript_to_ui_messages(lines)
    assert len(messages) == 1
    assert "media" not in messages[0]


def test_delta_with_non_string_text_is_ignored() -> None:
    lines = [{"event": "delta", "text": 42}]
    assert replay_transcript_to_ui_messages(lines) == []


def test_delta_after_user_builds_streaming_assistant_message() -> None:
    lines = [
        {"event": "user", "chat_id": "c1", "text": "hi"},
        {"event": "delta", "text": "Hel"},
        {"event": "delta", "text": "lo"},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"] == "Hello"
    # isStreaming e' un campo interno di fold, rimosso a fine replay.
    assert "isStreaming" not in messages[-1]


def test_file_edit_with_non_list_edits_is_ignored() -> None:
    lines = [{"event": "file_edit", "edits": "not-a-list"}]
    assert replay_transcript_to_ui_messages(lines) == []


def test_file_edit_with_empty_edits_is_ignored() -> None:
    lines = [{"event": "file_edit", "edits": []}]
    assert replay_transcript_to_ui_messages(lines) == []


def test_reasoning_delta_empty_chunk_is_ignored() -> None:
    lines = [{"event": "reasoning_delta", "text": ""}]
    assert replay_transcript_to_ui_messages(lines) == []


def test_reasoning_delta_non_string_chunk_is_ignored() -> None:
    lines = [{"event": "reasoning_delta", "text": None}]
    assert replay_transcript_to_ui_messages(lines) == []


def test_message_tool_hint_without_text_or_events_is_ignored() -> None:
    lines = [{"event": "message", "kind": "tool_hint"}]
    assert replay_transcript_to_ui_messages(lines) == []


def test_message_tool_hint_with_plain_text_creates_trace() -> None:
    lines = [{"event": "message", "kind": "tool_hint", "text": "Reading file.py"}]
    messages = replay_transcript_to_ui_messages(lines)
    assert len(messages) == 1
    assert messages[0]["kind"] == "trace"
    assert messages[0]["content"] == "Reading file.py"


def test_negative_latency_ms_is_not_stamped() -> None:
    lines = [
        {"event": "user", "chat_id": "c1", "text": "hi"},
        {"event": "message", "text": "answer"},
        {"event": "turn_end", "latency_ms": -5},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    assistant = [m for m in messages if m.get("role") == "assistant"][0]
    assert "latencyMs" not in assistant


def test_positive_latency_ms_is_stamped_on_turn_end() -> None:
    lines = [
        {"event": "user", "chat_id": "c1", "text": "hi"},
        {"event": "message", "text": "answer"},
        {"event": "turn_end", "latency_ms": 250},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    assistant = [m for m in messages if m.get("role") == "assistant"][0]
    assert assistant["latencyMs"] == 250


def test_media_answer_suppresses_deltas_until_turn_end() -> None:
    """Un messaggio finale con media sospende le delta successive fino a turn_end.

    Copre il ramo ``suppress_until_turn_end`` che altrimenti non e' esercitato
    dagli scenari gia' presenti in ``test_webui_transcript.py``.
    """
    lines = [
        {"event": "user", "chat_id": "c1", "text": "send me a picture"},
        {
            "event": "message",
            "text": "here you go",
            "media_urls": [{"url": "/api/media/sig/p", "name": "photo.png"}],
        },
        # Questa delta orfana arriva prima del turn_end: deve essere scartata.
        {"event": "delta", "text": "stray-chunk"},
        {"event": "reasoning_delta", "text": "stray-reasoning"},
        {"event": "turn_end"},
        # Dopo turn_end, una nuova delta ricomincia un nuovo messaggio.
        {"event": "delta", "text": "fresh-start"},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    contents = [m.get("content") for m in messages if m.get("role") == "assistant"]
    assert "stray-chunk" not in contents
    assert any(c == "fresh-start" for c in contents)


def test_stream_end_during_suppression_clears_buffer_without_new_message() -> None:
    lines = [
        {"event": "user", "chat_id": "c1", "text": "hi"},
        {
            "event": "message",
            "text": "answer with media",
            "media_urls": [{"url": "/api/media/sig/p", "name": "clip.mp4"}],
        },
        {"event": "stream_end", "text": "ignored-during-suppression"},
        {"event": "turn_end"},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    contents = [m.get("content") for m in messages if m.get("role") == "assistant"]
    assert "ignored-during-suppression" not in contents


def test_turn_fields_alias_reused_turn_id_after_close() -> None:
    """Un turn_id riutilizzato dopo il proprio turn_end riceve un alias univoco."""
    lines = [
        {"event": "user", "chat_id": "c1", "text": "first", "turn_id": "t1", "turn_phase": "user"},
        {"event": "turn_end", "turn_id": "t1"},
        # Stesso turn_id riusato: deve generare un turnId alias, non collassare
        # nello stesso turno gia' chiuso.
        {"event": "user", "chat_id": "c1", "text": "second", "turn_id": "t1", "turn_phase": "user"},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    user_messages = [m for m in messages if m.get("role") == "user"]
    assert len(user_messages) == 2
    assert user_messages[0]["turnId"] == "t1"
    assert user_messages[1]["turnId"] != "t1"
    assert user_messages[1]["turnId"].startswith("t1:replay:")


def test_augment_assistant_text_applied_only_to_non_trace_assistant_messages() -> None:
    lines = [
        {"event": "user", "chat_id": "c1", "text": "hi"},
        {"event": "message", "kind": "tool_hint", "text": "Reading file"},
        {"event": "message", "text": "final answer"},
    ]
    messages = replay_transcript_to_ui_messages(
        lines,
        augment_assistant_text=lambda text: f"[[{text}]]",
    )
    trace = [m for m in messages if m.get("kind") == "trace"][0]
    assistant = [m for m in messages if m.get("role") == "assistant" and m.get("kind") != "trace"][0]
    assert trace["content"] == "Reading file"  # non toccato dall'hook
    assert assistant["content"] == "[[final answer]]"


def test_empty_input_returns_empty_list() -> None:
    assert replay_transcript_to_ui_messages([]) == []


def test_session_boundary_survives_replay() -> None:
    """`/new` deve restare un separatore anche dopo un reload.

    Regressione trovata on-device: il flag era scritto nel transcript e gestito dal
    client sul percorso live, ma il replay ricostruisce i messaggi campo per campo e
    non lo copiava — dopo un riavvio dell'app il confine tornava a essere una bolla
    normale col testo "New session started." al posto della riga di separazione.
    """
    lines = [
        {"event": "message", "chat_id": "c1", "text": "New session started.", "session_boundary": True},
    ]
    messages = replay_transcript_to_ui_messages(lines)
    assert len(messages) == 1
    assert messages[0]["session_boundary"] is True


def test_ordinary_message_carries_no_session_boundary_flag() -> None:
    lines = [{"event": "message", "chat_id": "c1", "text": "ciao"}]
    messages = replay_transcript_to_ui_messages(lines)
    assert "session_boundary" not in messages[0]


def _reasoning_then_answer(
    reasoning_turn: str | None, answer_turn: str | None
) -> list[dict[str, object]]:
    """Un placeholder di solo-reasoning seguito da una risposta completa.

    È la coppia su cui ``absorb_complete`` decide se fondere o accodare, e
    l'unico posto dove l'asimmetria degli id è osservabile dall'API pubblica.
    """
    reasoning: dict[str, object] = {"event": "message", "kind": "reasoning", "text": "penso"}
    answer: dict[str, object] = {"event": "message", "text": "risposta"}
    if reasoning_turn:
        reasoning |= {"turn_id": reasoning_turn, "turn_phase": "reasoning"}
    if answer_turn:
        answer |= {"turn_id": answer_turn, "turn_phase": "answer"}
    return [reasoning, answer]


def test_answer_absorbs_reasoning_placeholder_of_the_same_turn() -> None:
    messages = replay_transcript_to_ui_messages(_reasoning_then_answer("t1", "t1"))
    assert len(messages) == 1
    assert messages[0]["content"] == "risposta"
    assert messages[0]["reasoning"] == "penso"


def test_answer_does_not_absorb_a_placeholder_of_another_turn() -> None:
    messages = replay_transcript_to_ui_messages(_reasoning_then_answer("t1", "t2"))
    assert len(messages) == 2
    assert messages[1]["content"] == "risposta"


def test_turnless_record_no_longer_absorbs_a_turn_annotated_one() -> None:
    """Regressione: bastava che *uno* dei due lati fosse senza id perché
    ``_same_turn`` dicesse "stesso turno", e record di turni estranei venivano
    assorbiti l'uno nell'altro. Ora l'asimmetria significa turni diversi."""
    assert len(replay_transcript_to_ui_messages(_reasoning_then_answer(None, "t2"))) == 2
    assert len(replay_transcript_to_ui_messages(_reasoning_then_answer("t1", None))) == 2


def test_two_records_without_any_turn_id_still_fold_as_before() -> None:
    """Compatibilità coi transcript scritti prima che il turno venisse annotato:
    id assente su entrambi i lati resta permissivo."""
    messages = replay_transcript_to_ui_messages(_reasoning_then_answer(None, None))
    assert len(messages) == 1
    assert messages[0]["content"] == "risposta"


def test_consecutive_proactive_answers_stay_distinct_messages() -> None:
    """Quattro avvisi proattivi consecutivi, ognuno col proprio turno, restano
    quattro bolle. È la forma che il transcript ha ora sul dispositivo (righe
    17720-17723), lì scritta senza ``turn_id``."""
    lines: list[dict[str, object]] = [
        {"event": "message", "text": f"avviso {i}", "turn_id": f"proactive:{i}",
         "turn_phase": "answer"}
        for i in range(4)
    ]
    messages = replay_transcript_to_ui_messages(lines)
    assert [m["content"] for m in messages] == [f"avviso {i}" for i in range(4)]


def test_the_text_of_a_long_work_keeps_its_id_and_its_role() -> None:
    """La bolla di lavoro della home (05/10/2026): l'annuncio e il resoconto di
    un ``/gardener`` portano l'id del lavoro, e l'annuncio dice di esserlo."""
    lines = [
        {"event": "message", "chat_id": "project:orto", "text": "Gardening orto...",
         "work_id": "gardener-1", "work_start": True},
        {"event": "message", "chat_id": "project:orto", "text": "The gardener read 2 lines.",
         "work_id": "gardener-1"},
        {"event": "message", "chat_id": "project:orto", "text": "altro"},
    ]
    start, report, other = replay_transcript_to_ui_messages(lines)
    assert (start["workId"], start.get("workStart")) == ("gardener-1", True)
    assert (report["workId"], report.get("workStart")) == ("gardener-1", None)
    assert "workId" not in other


def test_a_turn_that_ended_in_an_error_stays_an_error_bubble() -> None:
    """Il turno finito in errore del provider: il payload dal vivo porta
    ``error: {kind}`` (``ws_sender``), e riaprendo la chat la stessa chiave, con
    la stessa forma, arriva nel messaggio rigiocato. Un ``error`` storto non passa."""
    lines = [
        {"event": "message", "chat_id": "default", "text": "Non riesco a raggiungere il modello.",
         "error": {"kind": "connection"}},
        {"event": "message", "chat_id": "default", "text": "ciao"},
        {"event": "message", "chat_id": "default", "text": "x", "error": "connection"},
        {"event": "message", "chat_id": "default", "text": "y", "error": {"kind": ""}},
    ]
    failed, ok, bad_shape, empty_kind = replay_transcript_to_ui_messages(lines)
    assert failed["error"] == {"kind": "connection"}
    assert failed["content"] == "Non riesco a raggiungere il modello."
    assert "error" not in ok
    assert "error" not in bad_shape
    assert "error" not in empty_kind


def _answers(messages: list[dict]) -> list[str]:
    """I testi che la casa mostra: le risposte, non i pensieri ne' le righe degli strumenti."""
    return [
        m["content"] for m in messages
        if m.get("role") == "assistant" and m.get("kind") != "trace" and m.get("content")
    ]


def _before_a_tool(text: str) -> list[dict]:
    return [
        {"event": "user", "chat_id": "c1", "text": "dimmi"},
        {"event": "delta", "text": text},
        {"event": "stream_end", "text": text},
        {"event": "message", "kind": "progress", "text": "",
         "tool_events": [{"phase": "start", "name": "message"}]},
    ]


def test_text_before_a_tool_gives_way_to_the_answer() -> None:
    lines = [
        *_before_a_tool("ok, la cerco"),
        {"event": "delta", "text": "fatto"},
        {"event": "stream_end", "text": "fatto"},
        {"event": "turn_end"},
    ]
    assert _answers(replay_transcript_to_ui_messages(lines)) == ["fatto"]


def test_a_file_on_its_own_keeps_the_text_before_it() -> None:
    """Come dal vivo: il riassunto prima di ``message`` col solo file resta."""
    lines = [
        *_before_a_tool("ecco la ricerca: tre fonti"),
        {"event": "message", "text": "", "media_urls": [{"url": "/m/r.md", "name": "r.md"}]},
        {"event": "turn_end"},
    ]
    assert _answers(replay_transcript_to_ui_messages(lines)) == ["ecco la ricerca: tre fonti"]


def test_an_error_notice_keeps_the_answer_before_it() -> None:
    lines = [
        *_before_a_tool("si annaffia una volta a settimana"),
        {"event": "message", "text": "Ho finito i passaggi ma non…", "error": {"kind": "empty"}},
        {"event": "turn_end"},
    ]
    assert _answers(replay_transcript_to_ui_messages(lines)) == [
        "si annaffia una volta a settimana", "Ho finito i passaggi ma non…",
    ]


def test_a_file_after_the_answer_does_not_bring_the_old_line_back() -> None:
    lines = [
        *_before_a_tool("ok, la cerco"),
        {"event": "delta", "text": "ecco il file"},
        {"event": "stream_end", "text": "ecco il file"},
        {"event": "message", "text": "", "media_urls": [{"url": "/m/r.md", "name": "r.md"}]},
        {"event": "turn_end"},
    ]
    assert _answers(replay_transcript_to_ui_messages(lines)) == ["ecco il file"]
