"""Tracker di file-edit in streaming (estratto da file_edit_events.py).

`StreamingFileEditTracker` + le macchine di parsing incrementale del JSON in
arrivo (campi stringa, patch, stato per-file) che convertono i delta di
tool-call in eventi file-edit live/pending. Dipende dai builder/leaf di
``file_edit_events`` (import verso il basso) → nessun ciclo runtime.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from jenny.utils.file_edit_events import (
    _LIVE_EMIT_INTERVAL_S,
    _LIVE_EMIT_LINE_STEP,
    FileEditTracker,
    _resolve_raw_file_edit_path,
    _text_line_count,
    build_file_edit_error_event,
    build_file_edit_live_event,
    build_file_edit_pending_event,
    display_file_edit_path,
    prepare_file_edit_tracker,
    read_file_snapshot,
)


class StreamingFileEditTracker:
    """Track file-edit tool arguments while the model is still streaming them.

    Tool execution events only begin after the provider has completed the full
    function call.  For large ``write_file`` calls, the long wait is usually the
    model producing the JSON ``content`` argument.  Large ``edit_file`` calls
    can have the same wait while ``old_text`` / ``new_text`` stream in.  This
    tracker converts those argument deltas into approximate WebUI file-edit
    events before the final exact diff is available.
    """

    def __init__(
        self,
        *,
        workspace: Path | None,
        tools: Any,
        emit: Callable[[list[dict[str, Any]]], Awaitable[None]],
    ) -> None:
        self._workspace = workspace
        self._tools = tools
        self._emit = emit
        self._states: dict[str, _StreamingFileEditState] = {}

    async def update(self, payload: dict[str, Any]) -> None:
        key = _stream_key(payload)
        if not key:
            return
        state = self._states.get(key)
        if state is None:
            state = _StreamingFileEditState(key=key)
            self._states[key] = state

        state.apply_delta(payload)
        if state.name == "apply_patch":
            await self._update_apply_patch(state)
            return
        if state.name not in {"write_file", "edit_file"}:
            return
        if state.path is None:
            state.path = _extract_complete_json_string(state.arguments, "path")
        if state.path is None:
            added, deleted = state.live_diff_counts()
            now = time.monotonic()
            if state.should_emit_pending(added, deleted, now):
                state.mark_pending_emitted(added, deleted, now)
                await self._emit([build_file_edit_pending_event(
                    call_id=state.call_id or state.key,
                    tool_name=state.name,
                    added=added,
                    deleted=deleted,
                )])
            return
        if state.tracker is None:
            tool = self._tools.get(state.name) if hasattr(self._tools, "get") else None
            state.tracker = prepare_file_edit_tracker(
                call_id=state.call_id or state.key,
                tool_name=state.name,
                tool=tool,
                workspace=self._workspace,
                params={"path": state.path},
            )
            if state.tracker is None:
                return

        added, deleted = state.live_diff_counts()
        now = time.monotonic()
        if not state.should_emit(added, deleted, now):
            return
        state.mark_emitted(added, deleted, now)
        await self._emit([build_file_edit_live_event(
            state.tracker,
            added=added,
            deleted=deleted,
        )])

    async def _update_apply_patch(self, state: _StreamingFileEditState) -> None:
        if _json_bool_true(state.arguments, "dry_run"):
            return
        tool = self._tools.get("apply_patch") if hasattr(self._tools, "get") else None
        events: list[dict[str, Any]] = []
        now = time.monotonic()

        path_matches = list(re.finditer(r'"path"\s*:\s*"([^"]+)"', state.arguments))
        if not path_matches:
            return

        for i, m in enumerate(path_matches):
            raw_path = m.group(1)
            path = _resolve_raw_file_edit_path(tool, self._workspace, raw_path)
            if path is None:
                continue

            segment_start = m.start()
            segment_end = path_matches[i + 1].start() if i + 1 < len(path_matches) else len(state.arguments)
            segment = state.arguments[segment_start:segment_end]

            action_match = re.search(r'"action"\s*:\s*"(replace|add)"', segment)
            action = action_match.group(1) if action_match else "replace"

            old_text = _extract_json_string_prefix(segment, "old_text") or ""
            new_text = _extract_json_string_prefix(segment, "new_text") or ""

            added = _text_line_count(new_text) if action in ("replace", "add") else 0
            deleted = _text_line_count(old_text) if action == "replace" else 0

            file_state = state.patch_files.get(raw_path)
            if file_state is None:
                tracker = FileEditTracker(
                    call_id=state.call_id or state.key,
                    tool="apply_patch",
                    path=path,
                    display_path=display_file_edit_path(path, self._workspace),
                    before=read_file_snapshot(path),
                )
                file_state = _StreamingPatchFileState(tracker=tracker)
                state.patch_files[raw_path] = file_state
            if not file_state.should_emit(added, deleted, now):
                continue
            file_state.mark_emitted(added, deleted, now)
            events.append(build_file_edit_live_event(
                file_state.tracker,
                added=added,
                deleted=deleted,
            ))
        if events:
            await self._emit(events)

    async def flush(self) -> None:
        events: list[dict[str, Any]] = []
        now = time.monotonic()
        for state in self._states.values():
            for file_state in state.patch_files.values():
                added, deleted = file_state.last_added, file_state.last_deleted
                if not file_state.throttle.emitted_once:
                    continue
                if file_state.throttle.already_sent(added, deleted):
                    continue
                file_state.mark_emitted(added, deleted, now)
                events.append(build_file_edit_live_event(
                    file_state.tracker,
                    added=added,
                    deleted=deleted,
                ))
            if state.tracker is None:
                continue
            added, deleted = state.live_diff_counts()
            if state.live.already_sent(added, deleted):
                continue
            state.mark_emitted(added, deleted, now)
            events.append(build_file_edit_live_event(
                state.tracker,
                added=added,
                deleted=deleted,
            ))
        if events:
            await self._emit(events)

    def apply_final_call_ids(self, final_tool_calls: list[Any]) -> None:
        """Keep final start/end events keyed to any earlier streamed placeholder."""
        used_canonicals: set[str] = set()
        for tool_call in final_tool_calls:
            canonical = self.canonical_call_id_for(tool_call)
            if canonical and canonical not in used_canonicals:
                try:
                    tool_call.id = canonical
                    used_canonicals.add(canonical)
                except (AttributeError, TypeError):
                    pass

    def canonical_call_id_for(self, tool_call: Any) -> str | None:
        for state in self._states.values():
            if state.matches_final_tool_call(tool_call):
                return state.call_id or (state.tracker.call_id if state.tracker else None) or state.key
        return None

    async def error_unmatched(
        self,
        final_tool_calls: list[Any],
        error: str,
    ) -> None:
        """Mark streamed edits as failed when no final tool call will run."""
        events: list[dict[str, Any]] = []
        for state in self._states.values():
            for file_state in state.patch_files.values():
                if any(state.matches_final_tool_call(tool_call) for tool_call in final_tool_calls):
                    continue
                events.append(build_file_edit_error_event(file_state.tracker, error))
            if state.tracker is None:
                continue
            if any(state.matches_final_tool_call(tool_call) for tool_call in final_tool_calls):
                continue
            events.append(build_file_edit_error_event(state.tracker, error))
        if events:
            await self._emit(events)


@dataclass(slots=True)
class _StreamingJsonStringField:
    key: str
    scan_pos: int | None = None
    closed: bool = False
    escape: bool = False
    unicode_remaining: int = 0
    unicode_buffer: str = ""
    newline_count: int = 0
    has_chars: bool = False
    last_char_newline: bool = False
    last_char_cr: bool = False

    @property
    def line_count(self) -> int:
        if not self.has_chars:
            return 0
        return self.newline_count + (0 if self.last_char_newline else 1)

    def reset(self) -> None:
        self.scan_pos = None
        self.closed = False
        self.escape = False
        self.unicode_remaining = 0
        self.unicode_buffer = ""
        self.newline_count = 0
        self.has_chars = False
        self.last_char_newline = False
        self.last_char_cr = False

    def scan(self, source: str) -> None:
        if self.closed:
            return
        if self.scan_pos is None:
            match = re.search(rf'"{re.escape(self.key)}"\s*:\s*"', source)
            if match is None:
                return
            self.scan_pos = match.end()
        i = self.scan_pos
        while i < len(source):
            ch = source[i]
            if self.unicode_remaining > 0:
                self.unicode_buffer += ch
                self.unicode_remaining -= 1
                if self.unicode_remaining == 0:
                    try:
                        decoded = chr(int(self.unicode_buffer, 16))
                    except ValueError:
                        decoded = "x"
                    self.unicode_buffer = ""
                    self._mark_char(decoded)
                i += 1
                continue
            if self.escape:
                self.escape = False
                if ch == "u":
                    self.unicode_remaining = 4
                    self.unicode_buffer = ""
                elif ch == "n":
                    self._mark_char("\n")
                elif ch == "r":
                    self._mark_char("\r")
                else:
                    self._mark_char(ch)
                i += 1
                continue
            if ch == "\\":
                self.escape = True
                i += 1
                continue
            if ch == '"':
                self.closed = True
                i += 1
                break
            self._mark_char(ch)
            i += 1
        self.scan_pos = i

    def _mark_char(self, ch: str) -> None:
        self.has_chars = True
        if ch == "\r":
            self.newline_count += 1
            self.last_char_newline = True
            self.last_char_cr = True
        elif ch == "\n":
            if not self.last_char_cr:
                self.newline_count += 1
            self.last_char_newline = True
            self.last_char_cr = False
        else:
            self.last_char_newline = False
            self.last_char_cr = False


@dataclass(slots=True)
class _EmitThrottle:
    """Quando una riga «live» si riemette: la regola, una volta sola.

    La prima volta sempre; con gli stessi numeri mai; con un salto di almeno
    ``_LIVE_EMIT_LINE_STEP`` righe (aggiunte o tolte) subito; altrimenti non prima
    di ``_LIVE_EMIT_INTERVAL_S`` dall'ultima. Era scritta tre volte.
    """

    emitted_once: bool = False
    added: int = -1
    deleted: int = -1
    at: float = 0.0

    def should_emit(self, added: int, deleted: int, now: float) -> bool:
        if not self.emitted_once:
            return True
        if added == self.added and deleted == self.deleted:
            return False
        if max(abs(added - self.added), abs(deleted - self.deleted)) >= _LIVE_EMIT_LINE_STEP:
            return True
        return now - self.at >= _LIVE_EMIT_INTERVAL_S

    def mark(self, added: int, deleted: int, now: float) -> None:
        self.emitted_once = True
        self.added = added
        self.deleted = deleted
        self.at = now

    def already_sent(self, added: int, deleted: int) -> bool:
        """Questi numeri sono gli ultimi emessi (e qualcosa è stato emesso)."""
        return self.emitted_once and added == self.added and deleted == self.deleted


@dataclass(slots=True)
class _StreamingPatchFileState:
    tracker: FileEditTracker
    throttle: _EmitThrottle = field(default_factory=_EmitThrottle)
    # Gli ultimi numeri **visti**, emessi o no: ``flush`` li rilegge per chiudere
    # con il conto vero anche se l'ultimo aggiornamento era stato trattenuto.
    last_added: int = 0
    last_deleted: int = 0

    def should_emit(self, added: int, deleted: int, now: float) -> bool:
        self.last_added = added
        self.last_deleted = deleted
        return self.throttle.should_emit(added, deleted, now)

    def mark_emitted(self, added: int, deleted: int, now: float) -> None:
        self.last_added = added
        self.last_deleted = deleted
        self.throttle.mark(added, deleted, now)


@dataclass(slots=True)
class _StreamingFileEditState:
    key: str
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    path: str | None = None
    tracker: FileEditTracker | None = None
    content: _StreamingJsonStringField = field(
        default_factory=lambda: _StreamingJsonStringField("content")
    )
    old_text: _StreamingJsonStringField = field(
        default_factory=lambda: _StreamingJsonStringField("old_text")
    )
    new_text: _StreamingJsonStringField = field(
        default_factory=lambda: _StreamingJsonStringField("new_text")
    )
    patch_files: dict[str, _StreamingPatchFileState] = field(default_factory=dict)
    # Due ritmi separati: il conteggio prima che il path sia noto (``pending``) e
    # quello dopo, sul file vero (``live``).
    live: _EmitThrottle = field(default_factory=_EmitThrottle)
    pending: _EmitThrottle = field(default_factory=_EmitThrottle)

    def apply_delta(self, payload: dict[str, Any]) -> None:
        call_id = payload.get("call_id")
        if isinstance(call_id, str) and call_id:
            self.call_id = call_id
        name = payload.get("name")
        if isinstance(name, str) and name:
            self.name = name
        args = payload.get("arguments")
        if isinstance(args, str):
            self.arguments = args
            self.content.reset()
            self.old_text.reset()
            self.new_text.reset()
            self.patch_files.clear()
            return
        delta = payload.get("arguments_delta")
        if isinstance(delta, str) and delta:
            self.arguments += delta

    def live_diff_counts(self) -> tuple[int, int]:
        if self.name == "write_file":
            self.content.scan(self.arguments)
            return self.content.line_count, 0
        if self.name == "edit_file":
            self.old_text.scan(self.arguments)
            self.new_text.scan(self.arguments)
            return self.new_text.line_count, self.old_text.line_count
        return 0, 0

    def should_emit(self, added: int, deleted: int, now: float) -> bool:
        return self.live.should_emit(added, deleted, now)

    def mark_emitted(self, added: int, deleted: int, now: float) -> None:
        self.live.mark(added, deleted, now)

    def should_emit_pending(self, added: int, deleted: int, now: float) -> bool:
        return self.pending.should_emit(added, deleted, now)

    def mark_pending_emitted(self, added: int, deleted: int, now: float) -> None:
        self.pending.mark(added, deleted, now)

    def matches_final_tool_call(self, tool_call: Any) -> bool:
        call_id = getattr(tool_call, "id", None)
        canonical = self.call_id or (self.tracker.call_id if self.tracker else "")
        if isinstance(call_id, str) and call_id and canonical and call_id == canonical:
            return True
        name = getattr(tool_call, "name", None)
        if name != self.name:
            return False
        if self.name == "apply_patch":
            arguments = getattr(tool_call, "arguments", None)
            if not isinstance(arguments, dict):
                return False
            edits = arguments.get("edits")
            if not isinstance(edits, list):
                return False
            return '"edits"' in self.arguments
        arguments = getattr(tool_call, "arguments", None)
        if not isinstance(arguments, dict):
            return False
        path = arguments.get("path")
        if self.path is None and isinstance(path, str) and path:
            self.path = path
            return True
        return isinstance(path, str) and path == self.path


def _stream_key(payload: dict[str, Any]) -> str:
    index = payload.get("index")
    if isinstance(index, int):
        return f"idx:{index}"
    if isinstance(index, str) and index:
        return f"idx:{index}"
    call_id = payload.get("call_id")
    if isinstance(call_id, str) and call_id:
        return f"id:{call_id}"
    return ""


def _json_bool_true(source: str, key: str) -> bool:
    return re.search(rf'"{re.escape(key)}"\s*:\s*true\b', source) is not None


def _scan_json_string(source: str, key: str) -> tuple[str, bool] | None:
    """Il valore della stringa JSON di *key*, decodificato, e se è chiusa.

    ``None`` se la chiave non c'è. Altrimenti ``(testo, completa)``: il testo fin
    dove si è riusciti a leggere, e ``completa`` solo se si è arrivati alle
    virgolette di chiusura. Un ``\\u`` troncato o non esadecimale ferma la lettura
    lì (la stringa resta incompleta). ``\\b``, ``\\f`` e ``\\/`` rendono la lettera
    che segue il backslash: è il comportamento di sempre, e in un argomento di
    modifica file non compaiono.

    Uno scanner solo per i due usi (quanto testo c'è durante lo streaming, il
    valore a stringa chiusa): erano due copie che differivano solo in cosa
    rendere quando la stringa non è finita.
    """
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"', source)
    if match is None:
        return None
    out: list[str] = []
    i = match.end()
    escape = False
    while i < len(source):
        ch = source[i]
        if escape:
            escape = False
            if ch == "n":
                out.append("\n")
            elif ch == "r":
                out.append("\r")
            elif ch == "t":
                out.append("\t")
            elif ch == "u":
                digits = source[i + 1:i + 5]
                if len(digits) < 4:
                    return "".join(out), False
                try:
                    out.append(chr(int(digits, 16)))
                except ValueError:
                    return "".join(out), False
                i += 4
            else:
                out.append(ch)
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if ch == '"':
            return "".join(out), True
        out.append(ch)
        i += 1
    return "".join(out), False


def _extract_json_string_prefix(source: str, key: str) -> str | None:
    """Il testo della stringa letto finora, chiusa o no (durante lo streaming)."""
    scanned = _scan_json_string(source, key)
    return None if scanned is None else scanned[0]


def _extract_complete_json_string(source: str, key: str) -> str | None:
    """Il valore della stringa, solo se è chiusa."""
    scanned = _scan_json_string(source, key)
    return scanned[0] if scanned is not None and scanned[1] else None

