"""Il costo di una pagina di transcript deve essere limitato, non solo i messaggi.

``limit=160`` limita i messaggi resi e non dice niente sul lavoro: su un
transcript scritto un chunk di reasoning per record, una pagina da 160 messaggi
arrivava a 111.660 record, e ogni passata a valle li riattraversava tutti — 4,5 s
al primo caricamento sul device.
"""

from __future__ import annotations

import pytest

from jenny.webui.transcript import (
    _MAX_TRANSCRIPT_PAGE_RECORDS,
    _TRANSCRIPT_ACTIVE_CHUNK_ID,
    _count_anchor_messages,
    append_transcript_object,
    build_webui_thread_response,
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("jenny.config.paths.get_data_dir", lambda: tmp_path)
    # Turni a pezzetti come quelli di prima della fusione (``compact_turn``), che
    # restano tali finché la passata d'avvio non li compatta: il tetto è per loro.
    monkeypatch.setattr("jenny.webui.transcript_store.compact_turn", lambda *a: 0)
    return tmp_path


def _fat_turn(key: str, turn_id: str, chunks: int) -> None:
    """Un turno con reasoning scritto un record per chunk (forma legacy)."""
    append_transcript_object(key, {"event": "user", "chat_id": "t", "text": f"q {turn_id}"})
    for i in range(chunks):
        append_transcript_object(
            key, {"event": "reasoning_delta", "chat_id": "t", "turn_id": turn_id, "text": f"{i}"}
        )
    append_transcript_object(key, {"event": "reasoning_end", "chat_id": "t", "turn_id": turn_id})
    append_transcript_object(
        key, {"event": "stream_end", "chat_id": "t", "turn_id": turn_id, "text": "risposta"}
    )
    append_transcript_object(key, {"event": "turn_end", "chat_id": "t", "turn_id": turn_id})


# --- il tetto sui record -------------------------------------------------------


def test_record_budget_stops_adding_turns(data_dir) -> None:
    key = "websocket:budget"
    per_turn = _MAX_TRANSCRIPT_PAGE_RECORDS // 4
    for n in range(8):
        _fat_turn(key, f"t{n}", per_turn)

    data = build_webui_thread_response(key, limit=1000)

    # Senza il tetto entrerebbero tutti gli 8 turni (~160k record) perché il
    # limite in messaggi non è mai raggiunto.
    assert data is not None
    assert data["page"]["has_more_before"] is True
    assert data["page"]["before_cursor"]


def test_a_single_oversized_turn_is_never_truncated(data_dir) -> None:
    """Il tetto smette di aggiungere turni, non spezza quello in corso.

    Un turno più grande del budget deve comunque renderizzarsi per intero:
    troncarlo a metà mostrerebbe una risposta mutilata.
    """
    key = "websocket:one-big"
    _fat_turn(key, "solo", _MAX_TRANSCRIPT_PAGE_RECORDS * 2)

    data = build_webui_thread_response(key, limit=160)

    assert data is not None
    assert data["page"]["has_more_before"] is False
    # user + la risposta: il turno è tutto lì.
    roles = [m.get("role") for m in data["messages"]]
    assert "user" in roles and "assistant" in roles


def test_small_transcript_is_unaffected_by_the_budget(data_dir) -> None:
    """Nell'uso normale il tetto non deve mordere."""
    key = "websocket:small"
    for n in range(5):
        _fat_turn(key, f"t{n}", 3)

    data = build_webui_thread_response(key, limit=160)

    assert data is not None
    assert data["page"]["has_more_before"] is False
    assert data["page"]["before_cursor"] is None
    assert len([m for m in data["messages"] if m.get("role") == "user"]) == 5


# --- lettura dei chunk una volta sola -----------------------------------------


def test_active_chunk_is_read_once_per_request(data_dir, monkeypatch) -> None:
    """Prima lo stesso file veniva letto due volte per richiesta.

    Una da ``_chunk_turn_refs`` per contare i turni, una da
    ``_select_transcript_page`` per averli, senza cache in mezzo.
    """
    key = "websocket:cache"
    for n in range(3):
        _fat_turn(key, f"t{n}", 5)

    import jenny.webui.transcript as transcript_mod

    reads: list[str] = []
    original = transcript_mod._read_chunk_turns

    def counting(session_key, chunk_id):
        reads.append(chunk_id)
        return original(session_key, chunk_id)

    monkeypatch.setattr(transcript_mod, "_read_chunk_turns", counting)
    build_webui_thread_response(key, limit=160)

    assert reads.count(_TRANSCRIPT_ACTIVE_CHUNK_ID) == 1, reads


# --- la stima usata per dimensionare la pagina --------------------------------


def test_anchor_count_ignores_attachment_records() -> None:
    """Reasoning e delta si attaccano a un messaggio, non ne creano uno.

    È il motivo per cui contarli costruendo il fold era spreco: il numero serve
    solo a decidere quanti turni includere.
    """
    turn = [
        {"event": "user", "text": "q"},
        *[{"event": "reasoning_delta", "text": f"{i}"} for i in range(500)],
        {"event": "reasoning_end"},
        *[{"event": "delta", "text": f"{i}"} for i in range(50)],
        {"event": "stream_end", "text": "risposta"},
        {"event": "file_edit", "edits": []},
        {"event": "turn_end"},
    ]

    assert _count_anchor_messages(turn) == 2  # user + stream_end
