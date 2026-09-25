"""Append-only WebUI display transcript (JSONL), separate from agent session."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Callable, NamedTuple

from jenny.session.history_meta import is_synthetic_history_row

# Re-export per gli importatori esterni (media_gateway, ecc.). L'alias ridondante
# segnala a ruff che è un re-export intenzionale (non un import inutilizzato).
from jenny.webui.transcript_markdown import (
    rewrite_local_markdown_images as rewrite_local_markdown_images,
)
from jenny.webui.transcript_recorder import (
    WebUITranscriptRecorder as WebUITranscriptRecorder,
)

# Recorder layer. `_build_user_transcript_event` ha un chiamante interno
# (`_session_user_event`); `WebUITranscriptRecorder` è re-export per i channel.
from jenny.webui.transcript_recorder import (
    _build_user_transcript_event,
)
from jenny.webui.transcript_replay import replay_transcript_to_ui_messages

# Store layer (persistenza JSONL/segmenti). Re-export: gli importatori esterni e i
# test continuano a leggere questi simboli da ``webui.transcript``. L'alias ridondante
# marca i re-export che non hanno un chiamante interno (ruff non li pota).
from jenny.webui.transcript_store import (
    _TRANSCRIPT_ACTIVE_CHUNK_ID,
    _is_session_boundary_row,
    _is_user_transcript_row,
    _read_chunk_turns,
    _read_segment_manifest_entries,
    _rebuild_segment_manifest,
    _rotate_active_transcript_if_needed,
    _split_transcript_turns,
    read_transcript_lines,
    webui_transcript_path,
)
from jenny.webui.transcript_store import (
    append_transcript_object as append_transcript_object,
)
from jenny.webui.transcript_store import (
    delete_webui_transcript as delete_webui_transcript,
)
from jenny.webui.transcript_store import (
    webui_transcript_segments_dir as webui_transcript_segments_dir,
)

WEBUI_TRANSCRIPT_SCHEMA_VERSION = 3
_WEBUI_FORK_MARKER_EVENT = "fork_marker"
_DEFAULT_TRANSCRIPT_PAGE_LIMIT = 160
_MAX_TRANSCRIPT_PAGE_LIMIT = 1000
# Tetto sui record grezzi caricati in una pagina, indipendente da quello sui
# messaggi: è il costo reale della lettura, che il limite in messaggi non
# esprime. Dimensionato sopra il fabbisogno di una pagina piena scritta col
# reasoning coalescato (~2-3k record per ~160 messaggi), così nell'uso normale
# non morde mai, e limita i transcript vecchi scritti un chunk per volta.
_MAX_TRANSCRIPT_PAGE_RECORDS = 20_000
_TURN_DISPLAY_EVENTS: frozenset[str] = frozenset({
    "reasoning_delta",
    "reasoning_end",
    "delta",
    "stream_end",
    "message",
    "file_edit",
    "turn_end",
})


class _TranscriptTurnRef(NamedTuple):
    ordinal: int
    records: list[dict[str, Any]]


class _TranscriptChunkRef(NamedTuple):
    chunk_id: str
    start_ordinal: int
    turn_count: int
    user_count: int


def _encode_page_cursor(before_turn_ordinal: int) -> str:
    raw = json.dumps(
        {"before_turn": before_turn_ordinal},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_cursor(value: str | None) -> int | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    before_turn = data.get("before_turn")
    if (
        isinstance(before_turn, bool)
        or not isinstance(before_turn, int)
        or before_turn < 0
    ):
        return None
    return before_turn


def _coerce_page_limit(limit: int | None) -> int:
    if limit is None:
        return _DEFAULT_TRANSCRIPT_PAGE_LIMIT
    return max(1, min(_MAX_TRANSCRIPT_PAGE_LIMIT, int(limit)))


class _ChunkCache:
    """Legge i chunk del transcript una volta sola per richiesta.

    ``_read_chunk_turns`` rilegge e ri-parsa il file a ogni chiamata, e in una
    singola richiesta lo stesso chunk attivo veniva letto due volte: una da
    ``_chunk_turn_refs`` per contare i turni, una da ``_select_transcript_page``
    per averli. Lo scope è la richiesta, quindi non serve invalidazione: non
    esiste un istante in cui questa cache possa diventare stantia.
    """

    def __init__(self, session_key: str) -> None:
        self._session_key = session_key
        self._turns: dict[str, list[list[dict[str, Any]]]] = {}

    def turns(self, chunk_id: str) -> list[list[dict[str, Any]]]:
        cached = self._turns.get(chunk_id)
        if cached is None:
            cached = _read_chunk_turns(self._session_key, chunk_id)
            self._turns[chunk_id] = cached
        return cached


def _chunk_turn_refs(
    session_key: str, cache: _ChunkCache | None = None
) -> list[_TranscriptChunkRef]:
    _rotate_active_transcript_if_needed(session_key)
    cache = cache or _ChunkCache(session_key)
    refs: list[_TranscriptChunkRef] = []
    ordinal = 0
    for entry in _read_segment_manifest_entries(session_key):
        chunk_id = str(entry["id"])
        turn_count = int(entry["turn_count"])
        if turn_count <= 0:
            continue
        refs.append(_TranscriptChunkRef(chunk_id, ordinal, turn_count, int(entry["user_count"])))
        ordinal += turn_count
    if webui_transcript_path(session_key).is_file():
        active_turns = cache.turns(_TRANSCRIPT_ACTIVE_CHUNK_ID)
        active_turn_count = len(active_turns)
        if active_turn_count > 0:
            refs.append(
                _TranscriptChunkRef(
                    _TRANSCRIPT_ACTIVE_CHUNK_ID,
                    ordinal,
                    active_turn_count,
                    sum(1 for turn in active_turns for row in turn if _is_user_transcript_row(row)),
                ),
            )
    return refs


def _count_anchor_messages(turn: list[dict[str, Any]]) -> int:
    """Stima quanti messaggi UI produrrà *turn*, senza costruirli.

    Serve solo a dimensionare la pagina: il conteggio vero lo calcola
    ``build_webui_thread_response`` dal replay e sovrascrive
    ``loaded_message_count``. Prima questa stima si otteneva con un
    ``len(replay_transcript_to_ui_messages(turn))`` — l'intero fold costruito e
    buttato via per un numero, e poi rifatto a valle sugli stessi record.

    Conta i record che *ancorano* un messaggio; reasoning, delta e file_edit si
    attaccano a un messaggio esistente. È una sottostima per difetto, quindi al
    più include qualche turno in più: mai meno contenuto del richiesto.
    """
    total = 0
    for record in turn:
        event = record.get("event")
        if event in ("user", "stream_end", "message"):
            total += 1
    return total


def _count_user_messages_before_ordinal(
    session_key: str,
    chunks: list[_TranscriptChunkRef],
    before_ordinal: int,
    cache: _ChunkCache | None = None,
) -> int:
    total = 0
    cache = cache or _ChunkCache(session_key)
    for chunk in chunks:
        if before_ordinal <= chunk.start_ordinal:
            break
        local_end = min(chunk.turn_count, before_ordinal - chunk.start_ordinal)
        if local_end <= 0:
            continue
        if local_end >= chunk.turn_count:
            total += chunk.user_count
            continue
        turns = cache.turns(chunk.chunk_id)
        total += sum(
            1
            for turn in turns[:local_end]
            for row in turn
            if _is_user_transcript_row(row)
        )
    return total


def _is_session_boundary_turn(turn: list[dict[str, Any]]) -> bool:
    """Se *turn* contiene il confine lasciato da ``/new``.

    E' il turno in cui il modello ha smesso di ricordare quel che sta sopra, e da
    questa versione e' anche **dove comincia la cronologia visibile**: la pagina
    lo include e si ferma, cosi' la chat riaperta parte dal separatore invece di
    ripescare una conversazione che l'utente ha chiuso.

    Non e' una cancellazione e non deve diventarlo: ``has_more_before`` resta
    vero (``first_ref.ordinal > 0``), quindi lo scroll in su ricarica la sessione
    precedente come una pagina piu' vecchia — e' un'interruzione di pagina, che
    e' il contrario esatto di quel che faceva il vecchio ``/clear`` (schermo
    pulito e nulla di vero sotto).
    """
    return any(_is_session_boundary_row(row) for row in turn)


def _select_transcript_page(
    session_key: str,
    *,
    limit: int | None,
    before: str | None,
    _manifest_rebuilt: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    page_limit = _coerce_page_limit(limit)
    cache = _ChunkCache(session_key)
    chunks = _chunk_turn_refs(session_key, cache)
    total_turns = sum(chunk.turn_count for chunk in chunks)
    before_ordinal = _decode_page_cursor(before)
    upper_ordinal = total_turns if before_ordinal is None else min(before_ordinal, total_turns)
    selected: list[_TranscriptTurnRef] = []
    selected_message_count = 0
    selected_record_count = 0
    # Il confine di ``/new`` chiude la pagina: v. ``_is_session_boundary_turn``.
    hit_boundary = False

    for chunk in reversed(chunks):
        if chunk.start_ordinal >= upper_ordinal:
            continue
        local_upper = min(chunk.turn_count, upper_ordinal - chunk.start_ordinal)
        if local_upper <= 0:
            continue
        turns = cache.turns(chunk.chunk_id)
        if (
            chunk.chunk_id != _TRANSCRIPT_ACTIVE_CHUNK_ID
            and len(turns) != chunk.turn_count
            and not _manifest_rebuilt
        ):
            _rebuild_segment_manifest(session_key)
            return _select_transcript_page(
                session_key,
                limit=limit,
                before=before,
                _manifest_rebuilt=True,
            )
        local_upper = min(local_upper, len(turns))
        for turn_index in range(local_upper - 1, -1, -1):
            ordinal = chunk.start_ordinal + turn_index
            turn = turns[turn_index]
            selected.append(_TranscriptTurnRef(ordinal, turn))
            selected_message_count += _count_anchor_messages(turn)
            selected_record_count += len(turn)
            if _is_session_boundary_turn(turn):
                # Si include e si smette: il separatore e' la prima cosa che la
                # pagina mostra, ed e' anche la spiegazione di perche' sopra non
                # c'e' niente.
                hit_boundary = True
                break
            if selected_message_count >= page_limit:
                break
            # Secondo tetto, sui record grezzi. Il limite in messaggi non dice
            # nulla sul costo: un transcript scritto prima della coalescenza del
            # reasoning porta ~1400 record per segmento pensato, e una pagina da
            # 160 messaggi arrivava a 111k record che ogni passata a valle
            # riattraversava. Il turno in corso non si spezza mai a metà — si
            # smette di aggiungerne altri, e il client chiede il resto con
            # ``before_cursor``.
            if selected_record_count >= _MAX_TRANSCRIPT_PAGE_RECORDS:
                break
        if (
            hit_boundary
            or selected_message_count >= page_limit
            or selected_record_count >= _MAX_TRANSCRIPT_PAGE_RECORDS
        ):
            break

    selected_chronological = list(reversed(selected))
    lines = [record for ref in selected_chronological for record in ref.records]
    if not selected_chronological:
        return [], {
            "before_cursor": None,
            "has_more_before": False,
            "loaded_message_count": 0,
            "user_message_offset": 0,
        }

    first_ref = selected_chronological[0]
    has_more = first_ref.ordinal > 0
    page = {
        "before_cursor": _encode_page_cursor(first_ref.ordinal) if has_more else None,
        "has_more_before": has_more,
        "loaded_message_count": 0,
        "user_message_offset": _count_user_messages_before_ordinal(
            session_key,
            chunks,
            first_ref.ordinal,
            cache,
        ),
    }
    return lines, page


def _session_user_event(
    session_key: str,
    message: dict[str, Any],
) -> dict[str, Any] | None:
    if message.get("role") != "user":
        return None
    # Una riga che l'utente non ha scritto non e' una bolla da mostrargli, anche
    # se ne porta il ruolo: v. ``jenny.session.history_meta``.
    if is_synthetic_history_row(message):
        return None
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    media = message.get("media")
    chat_id = session_key.split(":", 1)[1] if ":" in session_key else session_key
    return _build_user_transcript_event(
        chat_id,
        text,
        media_paths=media if isinstance(media, list) else None,
    )


def _assistant_text_signature(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _session_backfill_turns(
    session_key: str,
    session_messages: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    turns: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    current_user: dict[str, Any] | None = None
    assistant_texts: list[str] = []

    def flush() -> None:
        if current_user is None:
            return
        signature = tuple(text for text in assistant_texts if text)
        if signature:
            turns.append((current_user, signature))

    for message in session_messages:
        role = message.get("role")
        if role == "user":
            flush()
            current_user = _session_user_event(session_key, message)
            assistant_texts = []
            continue
        if role == "assistant" and current_user is not None:
            text = _assistant_text_signature(message.get("content"))
            if text:
                assistant_texts.append(text)
    flush()
    return turns


def _transcript_turn_signature(records: list[dict[str, Any]]) -> tuple[str, ...]:
    texts: list[str] = []
    for message in replay_transcript_to_ui_messages(records):
        if message.get("role") != "assistant" or message.get("kind") == "trace":
            continue
        text = _assistant_text_signature(message.get("content"))
        if text:
            texts.append(text)
    return tuple(texts)


def _find_unique_session_turn(
    session_turns: list[tuple[dict[str, Any], tuple[str, ...]]],
    signature: tuple[str, ...],
    start: int,
) -> int | None:
    if not signature:
        return None
    found: int | None = None
    for index in range(start, len(session_turns)):
        if session_turns[index][1] != signature:
            continue
        if found is not None:
            return None
        found = index
    return found


def _with_backfilled_user(
    records: list[dict[str, Any]],
    user_event: dict[str, Any],
) -> list[dict[str, Any]]:
    for index, rec in enumerate(records):
        if rec.get("event") in _TURN_DISPLAY_EVENTS:
            return [*records[:index], dict(user_event), *records[index:]]
    return records


def _build_synthetic_transcript(
    session_key: str,
    session_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build synthetic transcript lines from session messages when the WebUI
    transcript file doesn't exist yet (e.g. migrated session, unified session
    with missing user events)."""
    chat_id = session_key.split(":", 1)[1] if ":" in session_key else session_key
    lines: list[dict[str, Any]] = []
    turn_counter = 0
    for msg in session_messages:
        role = msg.get("role")
        if role == "user":
            event = _session_user_event(session_key, msg)
            if event:
                event["turn_id"] = f"synthetic:{turn_counter}"
                event["turn_phase"] = "user"
                lines.append(event)
        elif role == "assistant":
            content = msg.get("content")
            text = content if isinstance(content, str) else ""
            if text.strip():
                lines.append({
                    "event": "message",
                    "chat_id": chat_id,
                    "text": text,
                    "kind": "message",
                    "turn_id": f"synthetic:{turn_counter}",
                    "turn_phase": "answer",
                })
                lines.append({
                    "event": "turn_end",
                    "chat_id": chat_id,
                    "turn_id": f"synthetic:{turn_counter}",
                    "turn_phase": "complete",
                })
                turn_counter += 1
    return lines


def _inject_missing_user_events_from_session(
    session_key: str,
    lines: list[dict[str, Any]],
    session_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Backfill user rows for legacy WebUI transcripts that only stored assistant streams."""
    if not lines or not session_messages:
        return lines
    session_turns = _session_backfill_turns(session_key, session_messages)
    if not session_turns:
        return lines

    out: list[dict[str, Any]] = []
    session_cursor = 0
    for turn in _split_transcript_turns(lines):
        has_user = any(rec.get("event") == "user" for rec in turn)
        signature = _transcript_turn_signature(turn)
        match_index = _find_unique_session_turn(session_turns, signature, session_cursor)
        if match_index is None:
            out.extend(turn)
            continue
        out.extend(turn if has_user else _with_backfilled_user(turn, session_turns[match_index][0]))
        session_cursor = match_index + 1
    return out


def _fork_boundary_message_count(lines: list[dict[str, Any]]) -> int | None:
    """Return the replayed UI message count before the first fork marker, if any."""
    for idx, rec in enumerate(lines):
        if rec.get("event") != _WEBUI_FORK_MARKER_EVENT:
            continue
        return len(replay_transcript_to_ui_messages(lines[:idx]))
    return None


def _has_pending_tool_calls(lines: list[dict[str, Any]]) -> bool:
    """Return True when the selected transcript tail looks like an unfinished turn."""
    for rec in reversed(lines):
        ev = rec.get("event")
        if ev == "turn_end":
            return False
        if ev == "user":
            return False
        if ev == "message":
            return rec.get("kind") in {"tool_hint", "progress", "reasoning"}
        if ev in {
            "delta",
            "stream_end",
            "reasoning_delta",
            "reasoning_end",
            "file_edit",
        }:
            return True
        if ev in {_WEBUI_FORK_MARKER_EVENT}:
            continue
    return False


def build_webui_thread_response(
    session_key: str,
    *,
    augment_user_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_text: Callable[[str], str] | None = None,
    session_messages: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    before: str | None = None,
    allow_empty: bool = False,
) -> dict[str, Any] | None:
    """Return a payload compatible with ``WebuiThreadPersistedPayload``.

    ``None`` significa "questa conversazione non esiste", e il chiamante HTTP ne
    fa un 404. Per una **sessione-progetto** appena creata quella risposta e'
    sbagliata: la conversazione esiste, e' solo vuota — e un 404 lascerebbe il
    client senza il payload da cui legge lo scope, quindi il chip tornerebbe a
    dire "personale" sopra la chat di un progetto. Chi sa di trovarsi in quel
    caso passa ``allow_empty=True`` e riceve un thread senza messaggi.
    """
    paginated = limit is not None or before is not None
    page: dict[str, Any] | None = None
    if paginated:
        lines, page = _select_transcript_page(session_key, limit=limit, before=before)
    else:
        lines = read_transcript_lines(session_key)
    if not lines and session_messages:
        lines = _build_synthetic_transcript(session_key, session_messages)
    if not lines:
        if not allow_empty:
            return None
        return {
            "schemaVersion": WEBUI_TRANSCRIPT_SCHEMA_VERSION,
            "sessionKey": session_key,
            "messages": [],
            "has_pending_tool_calls": False,
            "page": {"has_more_before": False, "loaded_message_count": 0},
        }
    lines = _inject_missing_user_events_from_session(session_key, lines, session_messages)
    fork_boundary = _fork_boundary_message_count(lines)
    msgs = replay_transcript_to_ui_messages(
        lines,
        augment_user_media=augment_user_media,
        augment_assistant_media=augment_assistant_media,
        augment_assistant_text=augment_assistant_text,
    )
    payload = {
        "schemaVersion": WEBUI_TRANSCRIPT_SCHEMA_VERSION,
        "sessionKey": session_key,
        "messages": msgs,
        "has_pending_tool_calls": _has_pending_tool_calls(lines),
    }
    if page is not None:
        page["loaded_message_count"] = len(msgs)
        payload["page"] = page
    if fork_boundary is not None:
        payload["fork_boundary_message_count"] = fork_boundary
    return payload
