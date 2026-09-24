"""Storage layer for the WebUI display transcript (estratto da ``transcript.py``).

Incapsula tutta la persistenza JSONL su disco: path helper, primitive di
lettura/scrittura, il layer a segmenti + manifest con rotazione del file attivo,
e le due utility pure (`_is_user_transcript_row`, `_split_transcript_turns`) da cui
dipende. Nessuna logica di replay/rendering vive qui: è il solo strato di I/O.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.config.paths import get_webui_dir
from jenny.session.manager import SessionManager
from jenny.utils.path import append_lines_durable, atomic_write

_MAX_TRANSCRIPT_FILE_BYTES = 8 * 1024 * 1024
_TARGET_ACTIVE_TRANSCRIPT_BYTES = _MAX_TRANSCRIPT_FILE_BYTES // 2
_TRANSCRIPT_SEGMENT_MANIFEST_VERSION = 2
_TRANSCRIPT_ACTIVE_CHUNK_ID = "active"
_TRANSCRIPT_SEGMENT_RE = re.compile(r"^\d{6}\.jsonl$")


def _is_user_transcript_row(row: dict[str, Any]) -> bool:
    return row.get("event") == "user" or row.get("role") == "user"


def _is_session_boundary_row(row: dict[str, Any]) -> bool:
    """La riga che ``/new`` lascia nel transcript.

    La scrive ``channels/ws_sender.py`` (``payload["session_boundary"]``) e la
    rilegge ``transcript_replay.py`` per renderla come separatore. Sta qui,
    accanto all'altro predicato di riga, perche' da questa versione decide anche
    **dove comincia la cronologia visibile**: v. ``_select_transcript_page``.
    """
    return row.get("session_boundary") is True


def _split_transcript_turns(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for rec in lines:
        current.append(rec)
        if rec.get("event") == "turn_end":
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def webui_transcript_path(session_key: str) -> Path:
    stem = SessionManager.safe_key(session_key)
    return get_webui_dir() / f"{stem}.jsonl"


def webui_transcript_segments_dir(session_key: str) -> Path:
    stem = SessionManager.safe_key(session_key)
    return get_webui_dir() / f"{stem}.segments"


def _webui_transcript_manifest_path(session_key: str) -> Path:
    return webui_transcript_segments_dir(session_key) / "manifest.json"


def _legacy_webui_thread_path(session_key: str) -> Path:
    stem = SessionManager.safe_key(session_key)
    return get_webui_dir() / f"{stem}.json"


def _record_json_line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def _read_transcript_file(path: Path) -> list[dict[str, Any]]:
    lines_out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("bad jsonl at {} line {}", path, line_no)
                    continue
                if isinstance(obj, dict):
                    lines_out.append(obj)
    except OSError as e:
        logger.warning("read transcript failed {}: {}", path, e)
        return []
    return lines_out


def _records_bytes(records: list[dict[str, Any]]) -> int:
    total = 0
    for record in records:
        total += len(_record_json_line(record).encode("utf-8")) + 1
    return total


def _flatten_turns(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [record for turn in turns for record in turn]


def _write_records_to_path(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for row in rows:
        raw = _record_json_line(row)
        if len(raw.encode("utf-8")) > _MAX_TRANSCRIPT_FILE_BYTES:
            raise ValueError("webui transcript line too large")
        lines.append(raw + "\n")
    atomic_write(path, "".join(lines))


def _segment_file_path(session_key: str, segment_id: str) -> Path:
    return webui_transcript_segments_dir(session_key) / f"{segment_id}.jsonl"


def _segment_ids_on_disk(session_key: str) -> list[str]:
    directory = webui_transcript_segments_dir(session_key)
    if not directory.is_dir():
        return []
    return sorted(
        path.stem
        for path in directory.iterdir()
        if path.is_file() and _TRANSCRIPT_SEGMENT_RE.fullmatch(path.name)
    )


def _segment_manifest_entry(session_key: str, segment_id: str) -> dict[str, Any]:
    path = _segment_file_path(session_key, segment_id)
    lines = _read_transcript_file(path)
    return {
        "id": segment_id,
        "bytes": path.stat().st_size if path.exists() else 0,
        "turn_count": len(_split_transcript_turns(lines)),
        "user_count": sum(1 for line in lines if _is_user_transcript_row(line)),
    }


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalize_manifest_entry(session_key: str, entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    segment_id = entry.get("id")
    if not isinstance(segment_id, str) or not _TRANSCRIPT_SEGMENT_RE.fullmatch(f"{segment_id}.jsonl"):
        return None
    segment_path = _segment_file_path(session_key, segment_id)
    values = {
        key: _non_negative_int(entry.get(key))
        for key in ("bytes", "turn_count", "user_count")
    }
    if not segment_path.is_file() or values["bytes"] != segment_path.stat().st_size:
        return None
    if values["turn_count"] is None or values["user_count"] is None:
        return None
    return {
        "id": segment_id,
        "bytes": values["bytes"],
        "turn_count": values["turn_count"],
        "user_count": values["user_count"],
    }


def _write_segment_manifest(session_key: str, segment_ids: list[str]) -> None:
    directory = webui_transcript_segments_dir(session_key)
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "version": _TRANSCRIPT_SEGMENT_MANIFEST_VERSION,
        "segments": [_segment_manifest_entry(session_key, segment_id) for segment_id in segment_ids],
    }
    path = _webui_transcript_manifest_path(session_key)
    # Il temporaneo di ``atomic_write`` ha nome unico per chiamata (due scrittori
    # concorrenti sullo stesso manifest non se lo portano via a vicenda) e resta
    # invisibile a ``_segment_ids_on_disk``, che filtra per regex.
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _rebuild_segment_manifest(session_key: str) -> list[str]:
    segment_ids = _segment_ids_on_disk(session_key)
    if segment_ids:
        _write_segment_manifest(session_key, segment_ids)
    else:
        _webui_transcript_manifest_path(session_key).unlink(missing_ok=True)
    return segment_ids


def _rebuilt_segment_manifest_entries(session_key: str) -> list[dict[str, Any]]:
    return [_segment_manifest_entry(session_key, segment_id) for segment_id in _rebuild_segment_manifest(session_key)]


def _read_segment_manifest_entries(session_key: str) -> list[dict[str, Any]]:
    directory = webui_transcript_segments_dir(session_key)
    if not directory.is_dir():
        return []
    path = _webui_transcript_manifest_path(session_key)
    if not path.is_file():
        return _rebuilt_segment_manifest_entries(session_key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_segments = data.get("segments") if isinstance(data, dict) else None
        if data.get("version") != _TRANSCRIPT_SEGMENT_MANIFEST_VERSION or not isinstance(raw_segments, list):
            return _rebuilt_segment_manifest_entries(session_key)
        entries: list[dict[str, Any]] = []
        for entry in raw_segments:
            normalized = _normalize_manifest_entry(session_key, entry)
            if normalized is None:
                return _rebuilt_segment_manifest_entries(session_key)
            entries.append(normalized)
        if [entry["id"] for entry in entries] != _segment_ids_on_disk(session_key):
            return _rebuilt_segment_manifest_entries(session_key)
        return entries
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return _rebuilt_segment_manifest_entries(session_key)


def _read_segment_ids(session_key: str) -> list[str]:
    return [entry["id"] for entry in _read_segment_manifest_entries(session_key)]


def _append_segment_turns(session_key: str, turns: list[list[dict[str, Any]]]) -> None:
    if not turns:
        return
    segment_ids = _read_segment_ids(session_key)
    next_id = int(segment_ids[-1]) + 1 if segment_ids else 1
    batch: list[list[dict[str, Any]]] = []
    batch_bytes = 0
    for turn in turns:
        turn_bytes = _records_bytes(turn)
        if batch and batch_bytes + turn_bytes > _MAX_TRANSCRIPT_FILE_BYTES:
            segment_id = f"{next_id:06d}"
            _write_records_to_path(_segment_file_path(session_key, segment_id), _flatten_turns(batch))
            segment_ids.append(segment_id)
            next_id += 1
            batch = []
            batch_bytes = 0
        batch.append(turn)
        batch_bytes += turn_bytes
    if batch:
        segment_id = f"{next_id:06d}"
        _write_records_to_path(_segment_file_path(session_key, segment_id), _flatten_turns(batch))
        segment_ids.append(segment_id)
    _write_segment_manifest(session_key, segment_ids)


def _rotate_active_transcript_if_needed(session_key: str) -> None:
    path = webui_transcript_path(session_key)
    if not path.is_file():
        return
    try:
        if path.stat().st_size <= _MAX_TRANSCRIPT_FILE_BYTES:
            return
    except OSError:
        return

    lines = _read_transcript_file(path)
    if not lines:
        return
    turns = _split_transcript_turns(lines)
    if len(turns) <= 1:
        return

    keep_start = len(turns) - 1
    keep_bytes = 0
    for idx in range(len(turns) - 1, -1, -1):
        turn_bytes = _records_bytes(turns[idx])
        if idx == len(turns) - 1 or keep_bytes + turn_bytes <= _TARGET_ACTIVE_TRANSCRIPT_BYTES:
            keep_start = idx
            keep_bytes += turn_bytes
            continue
        break

    moved = turns[:keep_start]
    kept = turns[keep_start:]
    if not moved:
        return
    _append_segment_turns(session_key, moved)
    _write_records_to_path(path, _flatten_turns(kept))


def _chunk_ids(session_key: str) -> list[str]:
    _rotate_active_transcript_if_needed(session_key)
    ids = _read_segment_ids(session_key)
    if webui_transcript_path(session_key).is_file():
        ids.append(_TRANSCRIPT_ACTIVE_CHUNK_ID)
    return ids


def _read_chunk_turns(session_key: str, chunk_id: str) -> list[list[dict[str, Any]]]:
    if chunk_id == _TRANSCRIPT_ACTIVE_CHUNK_ID:
        path = webui_transcript_path(session_key)
    else:
        path = _segment_file_path(session_key, chunk_id)
    if not path.is_file():
        return []
    return _split_transcript_turns(_read_transcript_file(path))


def read_transcript_lines(session_key: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for chunk_id in _chunk_ids(session_key):
        if chunk_id == _TRANSCRIPT_ACTIVE_CHUNK_ID:
            lines.extend(_read_transcript_file(webui_transcript_path(session_key)))
        else:
            lines.extend(_read_transcript_file(_segment_file_path(session_key, chunk_id)))
    return lines


def _append_to_active_transcript(session_key: str, obj: dict[str, Any]) -> None:
    raw = _record_json_line(obj)
    if len(raw.encode("utf-8")) > _MAX_TRANSCRIPT_FILE_BYTES:
        msg = "webui transcript line too large"
        raise ValueError(msg)
    path = webui_transcript_path(session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Un ``fsync`` fallito non fa fallire il turno: la riga è nel file.
    append_lines_durable(path, [raw], tolerate_fsync_error=True)


def append_transcript_object(session_key: str, obj: dict[str, Any]) -> None:
    _append_to_active_transcript(session_key, obj)
    if obj.get("event") == "turn_end":
        _rotate_active_transcript_if_needed(session_key)


def delete_webui_transcript(session_key: str) -> bool:
    removed = False
    for path in (webui_transcript_path(session_key), _legacy_webui_thread_path(session_key)):
        if not path.is_file():
            continue
        try:
            path.unlink()
            removed = True
        except OSError as e:
            logger.warning("Failed to delete webui transcript {}: {}", path, e)
    segments_dir = webui_transcript_segments_dir(session_key)
    if segments_dir.is_dir():
        try:
            shutil.rmtree(segments_dir)
            removed = True
        except OSError as e:
            logger.warning("Failed to delete webui transcript segments {}: {}", segments_dir, e)
    return removed
