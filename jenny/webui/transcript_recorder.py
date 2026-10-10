"""Recorder per il transcript WebUI (estratto da ``transcript.py``).

`WebUITranscriptRecorder` prepara e persiste gli eventi "wire" della WebUI senza
far filtrare le regole di presentazione nei channel. Dipende solo dallo store
(`append_transcript_object`) e dalle chiavi di metadata: nessun import verso
``transcript`` → nessun ciclo.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from loguru import logger

from jenny.webui.metadata import WEBUI_TURN_METADATA_KEY
from jenny.webui.transcript_store import append_transcript_object

_WEBUI_TURN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _normalize_webui_turn_id(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if _WEBUI_TURN_ID_RE.fullmatch(candidate):
            return candidate
    return str(uuid.uuid4())


def _build_user_transcript_event(
    chat_id: str,
    text: str,
    *,
    media_paths: list[Any] | None = None,
) -> dict[str, Any] | None:
    paths = [str(path) for path in (media_paths or []) if path]
    if not text and not paths:
        return None
    event: dict[str, Any] = {
        "event": "user",
        "chat_id": chat_id,
        "text": text,
    }
    if paths:
        event["media_paths"] = paths
    return event


class WebUITranscriptRecorder:
    """Prepare and persist WebUI wire events without leaking UI rules into channels."""

    def __init__(self, log: Any = logger) -> None:
        self._log = log
        self._turn_sequences: dict[tuple[str, str], int] = {}

    def client_turn_metadata(self, value: Any) -> dict[str, str]:
        return {WEBUI_TURN_METADATA_KEY: _normalize_webui_turn_id(value)}

    def prepare_event(
        self,
        chat_id: str,
        event: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
        phase: str | None = None,
    ) -> None:
        self._annotate_turn(chat_id, event, metadata, phase)

    def prepare_and_append(
        self,
        chat_id: str,
        event: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
        phase: str | None = None,
        transcript_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.prepare_event(
            chat_id,
            event,
            metadata=metadata,
            phase=phase,
        )
        record = dict(event)
        if transcript_overrides:
            record.update(transcript_overrides)
        self.append(chat_id, record)

    def append_user_message(
        self,
        chat_id: str,
        text: str,
        *,
        metadata: dict[str, Any],
        media_paths: list[str] | None = None,
    ) -> None:
        # I due comandi che nel registro sono solo rumore: quel che hanno fatto
        # lo dice gia' la riga che segue — la risposta di `/stop` (anche `/stop all`), il
        # separatore di sessione per il secondo. `/new` ci e' entrato quando il
        # confine e' diventato il pavimento della cronologia visibile: la sua
        # riga utente sta nello **stesso** turno del confine (i turni si spezzano
        # su `turn_end`, e un comando non ne apre uno), quindi non finisce sotto
        # il pavimento — e la chat appena azzerata si riapriva con un `/new`
        # appeso in cima. Gli altri comandi restano: `/model fast` o
        # `/goal <cosa>` sono decisioni, e rileggerle serve.
        if text.strip() in ("/stop", "/stop all", "/new") and not media_paths:
            return
        payload = _build_user_transcript_event(
            chat_id,
            text,
            media_paths=media_paths,
        )
        if payload is None:
            return
        self.prepare_and_append(chat_id, payload, metadata=metadata, phase="user")

    def append(self, chat_id: str, event: dict[str, Any]) -> None:
        try:
            dup = json.loads(json.dumps(event, ensure_ascii=False))
            # L'ora della riga, in ms epoch: la WebUI la mostra sotto ogni
            # messaggio. Solo su disco, non sul filo — dal vivo il client usa
            # il proprio orologio, che per un messaggio appena arrivato dice la
            # stessa cosa. Qui e non in `prepare_and_append` perche' e' l'unico
            # punto da cui passano tutte le righe.
            dup.setdefault("at", int(time.time() * 1000))
            append_transcript_object(f"websocket:{chat_id}", dup)
        except (OSError, ValueError, TypeError) as e:
            self._log.warning("webui transcript append failed: {}", e)

    def _next_turn_seq(self, chat_id: str, turn_id: str) -> int:
        key = (chat_id, turn_id)
        seq = self._turn_sequences.get(key, 0) + 1
        self._turn_sequences[key] = seq
        return seq

    def _annotate_turn(
        self,
        chat_id: str,
        event: dict[str, Any],
        metadata: dict[str, Any] | None,
        phase: str | None,
    ) -> None:
        if phase is None:
            return
        turn_id = (metadata or {}).get(WEBUI_TURN_METADATA_KEY)
        if not isinstance(turn_id, str) or not turn_id:
            return
        event["turn_id"] = turn_id
        event["turn_phase"] = phase
        event["turn_seq"] = self._next_turn_seq(chat_id, turn_id)
        if phase == "complete":
            self._turn_sequences.pop((chat_id, turn_id), None)
