"""Replay del transcript WebUI → messaggi UI (estratto da ``transcript.py``).

`replay_transcript_to_ui_messages` ripiega i record JSONL in dict a forma di
``UIMessage`` per la WebUI, rispecchiando il fold di ``useJennyStream.ts``.
Funzione autoconclusiva: dipende solo dagli helper tool-event (gia estratti) e
da stdlib. Nessun import verso ``transcript`` → nessun ciclo.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from jenny.webui.transcript_tool_events import (
    _file_edit_key,
    _filter_covered_file_edit_tool_events,
    _media_from_signed_urls,
    _merge_tool_events,
    _merge_unique_tool_trace_lines,
    _normalize_tool_events,
    _strip_covered_file_edit_tool_hints,
    _tool_event_file_edit_key,
    _tool_trace_lines_from_events,
)


def replay_transcript_to_ui_messages(
    lines: list[dict[str, Any]],
    *,
    augment_user_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_text: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Fold JSONL records into ``UIMessage``-shaped dicts for the WebUI.

    Mirrors the core fold in ``useJennyStream.ts`` (delta, reasoning,
    message+kind, turn_end). ``augment_user_media`` maps persisted filesystem
    paths to ``{url, name?}`` / attachment dicts the client expects. Assistant
    media gets a separate hook so replay can re-sign outbound attachments after
    a gateway restart instead of reusing stale process-local signed URLs.
    """
    messages: list[dict[str, Any]] = []
    buffer_message_id: str | None = None
    buffer_parts: list[str] = []
    suppress_until_turn_end = False
    # L'ultimo testo retrocesso a pensiero in questo turno, com'era: torna una
    # risposta se dopo arriva solo un file o un errore (``restore_demoted``).
    demoted: dict[str, Any] | None = None
    active_activity_segment_id: str | None = None
    active_file_edit_segment_id: str | None = None
    activity_segment_counter = 0
    _ts_base = int(time.time() * 1000)
    closed_turn_ids: set[str] = set()
    replay_turn_aliases: dict[str, str] = {}

    def _new_id(prefix: str, idx: int) -> str:
        return f"{prefix}-{idx}-{uuid.uuid4().hex[:8]}"

    def _new_activity_segment(*, activate: bool = True) -> str:
        nonlocal active_activity_segment_id, activity_segment_counter
        activity_segment_counter += 1
        segment_id = f"activity-{activity_segment_counter}"
        if activate:
            active_activity_segment_id = segment_id
        return segment_id

    def _turn_fields(rec: dict[str, Any], fallback_phase: str | None = None) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        turn_id = rec.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            if turn_id in closed_turn_ids:
                fields["turnId"] = replay_turn_aliases.setdefault(
                    turn_id,
                    f"{turn_id}:replay:{idx}",
                )
            else:
                fields["turnId"] = turn_id
        phase = rec.get("turn_phase")
        if isinstance(phase, str) and phase:
            fields["turnPhase"] = phase
        elif fallback_phase:
            fields["turnPhase"] = fallback_phase
        seq = rec.get("turn_seq")
        if isinstance(seq, (int, float)):
            fields["turnSeq"] = int(seq)
        # L'ora in cui la riga e' stata scritta (``at``, ms epoch), se c'e': i
        # transcript scritti prima del 06/10/2026 non ce l'hanno, e allora il
        # messaggio non ne ha una. Sta qui per la stessa ragione del confine qui
        # sotto: e' il punto attraversato da tutte le costruzioni di messaggio.
        # Per una risposta conta poi la chiusura del turno (``stamp_sent_at``);
        # senza, resta l'ora della riga che ne ha portato il testo.
        at = rec.get("at")
        if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0:
            fields["sentAt"] = int(at)
        if rec.get("session_boundary"):
            # Marcatore di `/new`. Il replay ricostruisce i messaggi campo per campo
            # invece di rigirare il record salvato, quindi un flag non elencato qui
            # esiste nel transcript e sparisce alla rilettura: il client riceveva il
            # confine come una normale bolla dell'assistente e lo rendeva come testo
            # ("New session started.") al posto del separatore. Sta in _turn_fields
            # perché è l'unico punto attraversato da tutte le costruzioni di messaggio.
            fields["session_boundary"] = True
        return fields

    def _same_turn(message: dict[str, Any], turn_fields: dict[str, Any]) -> bool:
        """Se i due record appartengano allo stesso turno.

        Un id mancante su **entrambi** i lati resta permissivo: è il caso dei
        transcript scritti prima che il turno venisse annotato, che devono
        continuare a ripiegarsi come sempre.

        Un id presente da un lato solo, invece, ora dice "turni diversi". Prima
        bastava che *uno* mancasse per far combaciare tutto, e i tre punti di
        fusione che consultano questo predicato — aggancio del reasoning,
        adozione del placeholder, ``absorb_complete`` — assorbivano record di
        turni estranei l'uno nell'altro. Non è un caso di confine: ogni append
        al transcript passa una ``phase`` non nulla, quindi dentro un turno l'id
        c'è per tutti i record o per nessuno, e l'asimmetria si presenta solo
        *fra* turni diversi. È esattamente lì che serve separarli.
        """
        turn_id = turn_fields.get("turnId")
        message_turn_id = message.get("turnId")
        if not turn_id and not message_turn_id:
            return True
        if not turn_id or not message_turn_id:
            return False
        return turn_id == message_turn_id

    def _ensure_activity_segment() -> str:
        return active_activity_segment_id or _new_activity_segment()

    def close_activity_for_answer() -> None:
        nonlocal active_activity_segment_id, active_file_edit_segment_id
        active_activity_segment_id = None
        active_file_edit_segment_id = None

    def close_file_edit_phase_before_activity() -> None:
        nonlocal active_activity_segment_id, active_file_edit_segment_id
        if active_file_edit_segment_id:
            active_activity_segment_id = None
            active_file_edit_segment_id = None

    def attach_reasoning_chunk(
        prev: list[dict[str, Any]],
        chunk: str,
        idx: int,
        turn_fields: dict[str, Any] | None = None,
    ) -> None:
        turn_fields = turn_fields or {}
        for i in range(len(prev) - 1, -1, -1):
            candidate = prev[i]
            if candidate.get("role") == "user":
                break
            if candidate.get("kind") == "trace":
                break
            if candidate.get("role") != "assistant":
                continue
            if not _same_turn(candidate, turn_fields):
                break
            content = str(candidate.get("content") or "")
            has_answer = len(content) > 0
            if (
                candidate.get("reasoningStreaming")
                or candidate.get("reasoning") is not None
                or has_answer
                or candidate.get("isStreaming")
            ):
                prev[i] = {
                    **candidate,
                    "reasoning": (str(candidate.get("reasoning") or "")) + chunk,
                    "reasoningStreaming": True,
                    "activitySegmentId": candidate.get("activitySegmentId") or _ensure_activity_segment(),
                    **turn_fields,
                }
                return
            break
        segment = _ensure_activity_segment()
        prev.append(
            {
                "id": _new_id("as", idx),
                "role": "assistant",
                "content": "",
                "isStreaming": True,
                "reasoning": chunk,
                "reasoningStreaming": True,
                "activitySegmentId": segment,
                **turn_fields,
                "createdAt": _ts_base + idx,
            },
        )

    def find_active_placeholder(
        prev: list[dict[str, Any]],
        turn_fields: dict[str, Any] | None = None,
    ) -> str | None:
        turn_fields = turn_fields or {}
        last = prev[-1] if prev else None
        if not last:
            return None
        if last.get("role") != "assistant" or last.get("kind") == "trace":
            return None
        if str(last.get("content") or ""):
            return None
        if not last.get("isStreaming"):
            return None
        if not _same_turn(last, turn_fields):
            return None
        return str(last.get("id"))

    def demote_interrupted_assistant(segment: str) -> None:
        nonlocal buffer_message_id, buffer_parts, demoted
        for i in range(len(messages) - 1, -1, -1):
            candidate = messages[i]
            if candidate.get("role") == "user":
                break
            content = candidate.get("content")
            if (
                candidate.get("role") != "assistant"
                or candidate.get("kind") == "trace"
                or not candidate.get("isStreaming")
                or not isinstance(content, str)
                or not content.strip()
                or candidate.get("media")
            ):
                continue
            reasoning_parts = [
                part
                for part in (candidate.get("reasoning"), content)
                if isinstance(part, str) and part.strip()
            ]
            demoted = candidate
            messages[i] = {
                **candidate,
                "content": "",
                "reasoning": "\n\n".join(reasoning_parts),
                "reasoningStreaming": False,
                "isStreaming": False,
                "activitySegmentId": candidate.get("activitySegmentId") or segment,
            }
            if buffer_message_id == candidate.get("id"):
                buffer_message_id = None
                buffer_parts = []
            return

    def restore_demoted() -> None:
        """Rimette come risposta l'ultimo testo retrocesso, se niente gli ha risposto dopo.

        Il testo prima di uno strumento diventa pensiero perche' lo sostituisce
        la risposta che segue. Un file da solo (``message`` col solo allegato) o
        un avviso d'errore non lo sostituiscono: quel testo era il riassunto del
        file, o la risposta prima che il turno finisse vuoto. Come dal vivo
        (``home-chat.js``, ``_keepPassing``).
        """
        nonlocal demoted
        original, demoted = demoted, None
        if original is None:
            return
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("id") != original.get("id"):
                continue
            answered = any(
                m.get("role") == "assistant"
                and m.get("kind") != "trace"
                and str(m.get("content") or "").strip()
                for m in messages[i + 1:]
            )
            if not answered:
                messages[i] = {**original, "isStreaming": False}
            return

    def close_reasoning(prev: list[dict[str, Any]]) -> None:
        for i in range(len(prev) - 1, -1, -1):
            if prev[i].get("reasoningStreaming"):
                prev[i] = {**prev[i], "reasoningStreaming": False}
                return

    def is_reasoning_only_placeholder(m: dict[str, Any]) -> bool:
        return (
            m.get("role") == "assistant"
            and m.get("kind") != "trace"
            and not str(m.get("content") or "").strip()
            and bool(m.get("reasoning"))
            and not m.get("reasoningStreaming")
            and not m.get("media")
        )

    def is_tool_trace_at(index: int) -> bool:
        m = messages[index] if 0 <= index < len(messages) else None
        return bool(m and m.get("kind") == "trace")

    def prune_reasoning_only() -> None:
        nonlocal messages
        kept: list[dict[str, Any]] = []
        for i, m in enumerate(messages):
            if is_reasoning_only_placeholder(m) and not is_tool_trace_at(i + 1):
                continue
            kept.append(m)
        messages = kept

    def stamp_latency(latency_ms: int) -> None:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and messages[i].get("kind") != "trace":
                messages[i] = {
                    **messages[i],
                    "latencyMs": latency_ms,
                    "isStreaming": False,
                }
                return

    def stamp_sent_at(sent_at: int) -> None:
        """L'ora di una risposta e' quella in cui il turno si chiude: e' quando
        la coda compare sotto la bolla, anche dal vivo."""
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                return
            if messages[i].get("role") == "assistant" and messages[i].get("kind") != "trace":
                messages[i] = {**messages[i], "sentAt": sent_at}
                return

    def absorb_complete(extra: dict[str, Any], idx: int) -> None:
        nonlocal active_activity_segment_id, active_file_edit_segment_id
        last = messages[-1] if messages else None
        if last and is_reasoning_only_placeholder(last) and _same_turn(last, extra):
            messages[-1] = {
                **last,
                **extra,
                "isStreaming": False,
                "reasoningStreaming": False,
            }
        else:
            messages.append(
                {
                    "id": _new_id("as", idx),
                    "role": "assistant",
                    "createdAt": _ts_base + idx,
                    **extra,
                },
            )
        active_activity_segment_id = None
        active_file_edit_segment_id = None

    def find_file_edit_trace_index(
        segment: str | None,
        edits: list[dict[str, Any]],
    ) -> int | None:
        incoming_keys = {_file_edit_key(edit) for edit in edits if isinstance(edit, dict)}
        for i in range(len(messages) - 1, -1, -1):
            candidate = messages[i]
            if candidate.get("role") == "user":
                break
            if candidate.get("kind") != "trace":
                continue
            if segment and candidate.get("activitySegmentId") == segment:
                return i
            existing_edits = candidate.get("fileEdits")
            if isinstance(existing_edits, list):
                for existing in existing_edits:
                    if isinstance(existing, dict) and _file_edit_key(existing) in incoming_keys:
                        return i
            existing_tool_events = candidate.get("toolEvents")
            if isinstance(existing_tool_events, list):
                for event in existing_tool_events:
                    if not isinstance(event, dict):
                        continue
                    key = _tool_event_file_edit_key(event)
                    if key and key in incoming_keys:
                        return i
        return None

    def upsert_file_edits(
        edits: list[dict[str, Any]],
        idx: int,
        turn_fields: dict[str, Any] | None = None,
    ) -> None:
        nonlocal active_file_edit_segment_id
        turn_fields = turn_fields or {}
        if not edits:
            return
        segment = active_file_edit_segment_id
        if not segment:
            segment = _new_activity_segment(activate=False)
            active_file_edit_segment_id = segment
        demote_interrupted_assistant(segment)
        target_index = find_file_edit_trace_index(segment, edits)
        if target_index is not None:
            last = messages[target_index]
            segment = str(last.get("activitySegmentId") or segment or _new_activity_segment(activate=False))
            active_file_edit_segment_id = segment
            last = _strip_covered_file_edit_tool_hints(last, edits)
        else:
            if not segment:
                segment = _new_activity_segment(activate=False)
            active_file_edit_segment_id = segment
            messages.append(
                {
                    "id": _new_id("tr", idx),
                    "role": "tool",
                    "kind": "trace",
                    "content": "",
                    "traces": [],
                    "fileEdits": [],
                    "activitySegmentId": segment,
                    **turn_fields,
                    "createdAt": _ts_base + idx,
                },
            )
            target_index = len(messages) - 1
            last = messages[target_index]
        if not segment:
            segment = _new_activity_segment(activate=False)
            active_file_edit_segment_id = segment
        existing = list(last.get("fileEdits") or [])
        index_by_key = {
            _file_edit_key(edit): pos
            for pos, edit in enumerate(existing)
            if isinstance(edit, dict)
        }
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            key = _file_edit_key(edit)
            if key in index_by_key:
                pos = index_by_key[key]
                merged = {**existing[pos], **edit}
                if edit.get("path") and not edit.get("pending"):
                    merged.pop("pending", None)
                existing[pos] = merged
            else:
                index_by_key[key] = len(existing)
                existing.append(dict(edit))
        messages[target_index] = {
            **last,
            "fileEdits": existing,
            "activitySegmentId": last.get("activitySegmentId") or segment,
            **turn_fields,
        }

    def _on_user(rec: dict[str, Any], idx: int) -> None:
        nonlocal active_activity_segment_id, active_file_edit_segment_id, buffer_message_id, buffer_parts, demoted
        demote_interrupted_assistant(_ensure_activity_segment())
        demoted = None
        buffer_message_id = None
        buffer_parts = []
        active_activity_segment_id = None
        active_file_edit_segment_id = None
        text = rec.get("text")
        text_s = text if isinstance(text, str) else ""
        media_paths = rec.get("media_paths")
        paths: list[str] = []
        if isinstance(media_paths, list):
            paths = [str(p) for p in media_paths if p]
        media_att: list[dict[str, Any]] | None = None
        if paths and augment_user_media is not None:
            media_att = augment_user_media(paths)
        row: dict[str, Any] = {
            "id": _new_id("u", idx),
            "role": "user",
            "content": text_s,
            **_turn_fields(rec, "user"),
            "createdAt": _ts_base + idx,
        }
        origin = rec.get("origin")
        if isinstance(origin, str) and origin:
            row["origin"] = origin
        device = rec.get("device")
        if isinstance(device, str) and device:
            row["device"] = device
        if media_att:
            row["media"] = media_att
            if all(m.get("kind") == "image" for m in media_att):
                row["images"] = [{"url": m.get("url"), "name": m.get("name")} for m in media_att]
        messages.append(row)
        return

    def _on_file_edit(rec: dict[str, Any], idx: int) -> None:
        raw_edits = rec.get("edits")
        if isinstance(raw_edits, list):
            upsert_file_edits(
                [e for e in raw_edits if isinstance(e, dict)],
                idx,
                _turn_fields(rec, "activity"),
            )
        return

    def _on_delta(rec: dict[str, Any], idx: int) -> None:
        nonlocal buffer_message_id
        if suppress_until_turn_end:
            return
        chunk = rec.get("text")
        if not isinstance(chunk, str):
            return
        close_activity_for_answer()
        turn_fields = _turn_fields(rec, "answer")
        adopted = find_active_placeholder(messages, turn_fields) if buffer_message_id is None else None
        if buffer_message_id is None:
            if adopted:
                buffer_message_id = adopted
            else:
                buffer_message_id = _new_id("buf", idx)
                messages.append(
                    {
                        "id": buffer_message_id,
                        "role": "assistant",
                        "content": "",
                        "isStreaming": True,
                        **_turn_fields(rec, "answer"),
                        "createdAt": _ts_base + idx,
                    },
                )
        buffer_parts.append(chunk)
        combined = "".join(buffer_parts)
        for i, m in enumerate(messages):
            if m.get("id") == buffer_message_id:
                messages[i] = {
                    **m,
                    "content": combined,
                    "isStreaming": True,
                    **_turn_fields(rec, "answer"),
                }
                break
        return

    def _on_stream_end(rec: dict[str, Any], idx: int) -> None:
        nonlocal buffer_message_id, buffer_parts
        if suppress_until_turn_end:
            buffer_message_id = None
            buffer_parts = []
            return
        final_text = rec.get("text")
        if isinstance(final_text, str):
            if buffer_message_id is None:
                buffer_message_id = _new_id("buf", idx)
                messages.append(
                    {
                        "id": buffer_message_id,
                        "role": "assistant",
                        "content": final_text,
                        "isStreaming": True,
                        **_turn_fields(rec, "answer"),
                        "createdAt": _ts_base + idx,
                    },
                )
            else:
                for i, m in enumerate(messages):
                    if m.get("id") == buffer_message_id:
                        messages[i] = {
                            **m,
                            "content": final_text,
                            "isStreaming": True,
                            **_turn_fields(rec, "answer"),
                        }
                        break
        buffer_message_id = None
        buffer_parts = []
        return

    def _on_reasoning_delta(rec: dict[str, Any], idx: int) -> None:
        if suppress_until_turn_end:
            return
        chunk = rec.get("text")
        if not isinstance(chunk, str) or not chunk:
            return
        close_file_edit_phase_before_activity()
        attach_reasoning_chunk(messages, chunk, idx, _turn_fields(rec, "reasoning"))
        return

    def _on_reasoning_end(rec: dict[str, Any], idx: int) -> None:
        if suppress_until_turn_end:
            return
        close_reasoning(messages)
        return

    def _on_message(rec: dict[str, Any], idx: int) -> None:
        nonlocal buffer_message_id, buffer_parts, suppress_until_turn_end
        if suppress_until_turn_end and rec.get("kind") in (
            "tool_hint",
            "progress",
            "reasoning",
        ):
            return
        kind = rec.get("kind")
        if kind == "reasoning":
            line = rec.get("text")
            if not isinstance(line, str) or not line:
                return
            close_file_edit_phase_before_activity()
            attach_reasoning_chunk(messages, line, idx, _turn_fields(rec, "reasoning"))
            close_reasoning(messages)
            return
        if kind in ("tool_hint", "progress"):
            structured_events = _normalize_tool_events(rec.get("tool_events"))
            visible_structured_events = _filter_covered_file_edit_tool_events(messages, structured_events)
            structured = _tool_trace_lines_from_events(visible_structured_events)
            text = rec.get("text")
            if structured:
                trace_lines = structured
            elif structured_events:
                trace_lines = []
            elif isinstance(text, str) and text:
                trace_lines = [text]
            else:
                trace_lines = []
            if not trace_lines:
                return
            segment = _ensure_activity_segment()
            demote_interrupted_assistant(segment)
            last = messages[-1] if messages else None
            if (
                last
                and last.get("kind") == "trace"
                and not last.get("isStreaming")
                and (last.get("activitySegmentId") in (None, segment))
            ):
                prev_traces = list(last.get("traces") or [last.get("content")])
                if structured:
                    merged_traces, added = _merge_unique_tool_trace_lines(prev_traces, structured)
                    if not added and not visible_structured_events:
                        return
                else:
                    merged_traces = prev_traces + trace_lines
                merged = {
                    **last,
                    "traces": merged_traces,
                    "content": merged_traces[-1],
                    "toolEvents": _merge_tool_events(last.get("toolEvents"), visible_structured_events)
                    if visible_structured_events
                    else last.get("toolEvents"),
                    "activitySegmentId": last.get("activitySegmentId") or segment,
                    **_turn_fields(rec, "activity"),
                }
                messages[-1] = merged
            else:
                messages.append(
                    {
                        "id": _new_id("tr", idx),
                        "role": "tool",
                        "kind": "trace",
                        "content": trace_lines[-1],
                        "traces": trace_lines,
                        **({"toolEvents": visible_structured_events} if visible_structured_events else {}),
                        "activitySegmentId": segment,
                        **_turn_fields(rec, "activity"),
                        "createdAt": _ts_base + idx,
                    },
                )
            return

        buffer_message_id = None
        buffer_parts = []
        text = rec.get("text")
        content_s = text if isinstance(text, str) else ""
        media: list[dict[str, Any]] = []
        raw_media = rec.get("media")
        raw_media_list = raw_media if isinstance(raw_media, list) else []
        media_paths = [path for path in raw_media_list if isinstance(path, str) and path]
        if media_paths and augment_assistant_media is not None:
            media = augment_assistant_media(media_paths)
        if not media and (not media_paths or augment_assistant_media is None):
            media = _media_from_signed_urls(rec.get("media_urls"))
        extra: dict[str, Any] = {"content": content_s}
        if media:
            extra["media"] = media
        origin = rec.get("origin")
        if isinstance(origin, str) and origin:
            extra["origin"] = origin
        lat = rec.get("latency_ms")
        if isinstance(lat, (int, float)) and lat >= 0:
            extra["latencyMs"] = int(lat)
        work_id = rec.get("work_id")
        if isinstance(work_id, str) and work_id:
            # Il testo e' l'annuncio o il resoconto di un lavoro lungo (la bolla
            # di lavoro della home): la cronologia della home ne tiene uno solo.
            extra["workId"] = work_id
            if rec.get("work_start"):
                extra["workStart"] = True
        error = rec.get("error")
        if isinstance(error, dict) and isinstance(error.get("kind"), str) and error["kind"]:
            # La bolla d'errore resta tale anche riaprendo la chat: stessa
            # chiave e stessa forma del payload dal vivo (``ws_sender``).
            extra["error"] = {"kind": error["kind"]}
        extra.update(_turn_fields(rec, "answer"))
        if "error" in extra or (media and not content_s.strip()):
            restore_demoted()
        absorb_complete(extra, idx)
        if media:
            suppress_until_turn_end = True
        return

    def _on_turn_end(rec: dict[str, Any], idx: int) -> None:
        nonlocal active_activity_segment_id, active_file_edit_segment_id, buffer_message_id, buffer_parts, suppress_until_turn_end, demoted
        suppress_until_turn_end = False
        demoted = None
        active_activity_segment_id = None
        active_file_edit_segment_id = None
        turn_id = rec.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            if turn_id in replay_turn_aliases:
                replay_turn_aliases.pop(turn_id, None)
            else:
                closed_turn_ids.add(turn_id)
        for i, m in enumerate(messages):
            if m.get("isStreaming"):
                messages[i] = {**m, "isStreaming": False}
        prune_reasoning_only()
        lat = rec.get("latency_ms")
        if isinstance(lat, (int, float)) and lat >= 0:
            stamp_latency(int(lat))
        at = rec.get("at")
        if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0:
            stamp_sent_at(int(at))
        buffer_message_id = None
        buffer_parts = []
        return

    # Un ramo per tipo di evento, ciascuno leggibile per conto suo. Erano
    # otto ``if ev == …`` dentro un ciclo di 250 righe, dove per capire cosa
    # facesse uno bisognava scorrere gli altri sette. Il corpo è lo stesso:
    # i ``continue`` di livello ramo sono diventati ``return`` (stesso
    # significato — «questo record è finito»), i ``break`` erano tutti dentro
    # cicli interni e restano dove sono.
    handlers: dict[str, Callable[[dict[str, Any], int], None]] = {
        "user": _on_user,
        "file_edit": _on_file_edit,
        "delta": _on_delta,
        "stream_end": _on_stream_end,
        "reasoning_delta": _on_reasoning_delta,
        "reasoning_end": _on_reasoning_end,
        "message": _on_message,
        "turn_end": _on_turn_end,
    }

    for idx, rec in enumerate(lines):
        ev = rec.get("event")
        # ``isinstance`` prima del lookup: il vecchio ciclo confrontava con
        # ``==``, che tollera qualunque cosa, mentre una chiave non hashabile
        # (``"event": []``, da una riga corrotta) farebbe sollevare ``.get``.
        # Il file sta su disco: una riga rotta deve costare quella riga, non
        # l'intera conversazione.
        handler = handlers.get(ev) if isinstance(ev, str) else None
        if handler is not None:
            handler(rec, idx)

    for i, m in enumerate(messages):
        if (
            augment_assistant_text is not None
            and m.get("role") == "assistant"
            and m.get("kind") != "trace"
            and isinstance(m.get("content"), str)
        ):
            messages[i] = {**m, "content": augment_assistant_text(m["content"])}
        m.pop("isStreaming", None)
        m.pop("reasoningStreaming", None)
    return messages
