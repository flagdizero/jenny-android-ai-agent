"""Session management for conversation history."""

import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.session.history_meta import is_synthetic_history_row
from jenny.utils.helpers import (
    channel_delivery_aware_user_start,
    ensure_dir,
    estimate_message_tokens,
    find_legal_message_start,
    image_placeholder_text,
    recent_message_start_index,
    safe_filename,
)
from jenny.utils.path import atomic_write

FILE_MAX_MESSAGES = 2000
_MESSAGE_TIME_PREFIX_RE = re.compile(r"^\[Message Time: [^\]]+\]\n?")
_LOCAL_IMAGE_BREADCRUMB_RE = re.compile(r"^\[image: (?:/|~)[^\]]+\]\s*$")
_TOOL_CALL_ECHO_RE = re.compile(r'^\s*message\([^)]*\)\s*$')


def _sanitize_assistant_replay_text(content: str) -> str:
    """Remove internal replay artifacts that the model may have copied before.

    These strings are useful as runtime/session metadata, but when they appear
    in assistant examples they become demonstrations for the model to repeat.
    """
    content = _MESSAGE_TIME_PREFIX_RE.sub("", content, count=1)
    lines = [
        line
        for line in content.splitlines()
        if not _LOCAL_IMAGE_BREADCRUMB_RE.match(line)
        and not _TOOL_CALL_ECHO_RE.match(line)
    ]
    return "\n".join(lines).strip()


@dataclass
class Session:
    """A conversation session."""

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def __post_init__(self) -> None:
        # An out-of-range offset (corrupt metadata) would hide all history; reset it.
        if (
            isinstance(self.last_consolidated, bool)
            or not isinstance(self.last_consolidated, int)
            or not 0 <= self.last_consolidated <= len(self.messages)
        ):
            self.last_consolidated = 0

    @staticmethod
    def _annotate_message_time(message: dict[str, Any], content: Any) -> Any:
        """Expose persisted turn timestamps to the model for relative-date reasoning.

        Annotating *every* assistant turn trains the model (via in-context
        demonstrations) to start its own replies with the same
        ``[Message Time: ...]`` prefix, which leaks metadata back to the user.
        We therefore only annotate user turns. User-side stamps are enough to
        pin adjacent assistant replies for relative-time reasoning, including
        proactive messages the user replies to later.
        """
        timestamp = message.get("timestamp")
        if not timestamp or not isinstance(content, str):
            return content
        role = message.get("role")
        if role != "user":
            return content
        return f"[Message Time: {timestamp}]\n{content}"

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = 120,
        *,
        max_tokens: int = 0,
        include_timestamps: bool = False,
        extend_to_user: bool = False,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input.

        History is sliced by message count first (``max_messages``), then by
        token budget from the tail (``max_tokens``) when provided.
        """
        unconsolidated = self.messages[self.last_consolidated:]
        max_messages = max_messages if max_messages > 0 else 120
        start_idx = recent_message_start_index(
            unconsolidated,
            max_messages,
            extend_to_user=extend_to_user,
        )
        sliced = unconsolidated[start_idx:]

        # Avoid starting mid-turn when possible, except for proactive
        # assistant deliveries that the user may be replying to.
        user_start = channel_delivery_aware_user_start(sliced)
        if user_start is not None:
            sliced = sliced[user_start:]

        # Drop orphan tool results at the front.
        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            if message.get("_command"):
                continue
            content = message.get("content", "")
            role = message.get("role")
            if role == "assistant" and isinstance(content, str):
                content = _sanitize_assistant_replay_text(content)
            # Synthesize an ``[image: path]`` breadcrumb from the persisted
            # ``media`` kwarg so LLM replay still sees *something* where the
            # image used to be. Without this, an image-only user turn
            # replays as an empty user message — the assistant's reply then
            # looks like it's responding to nothing.
            media = message.get("media")
            if role == "user" and isinstance(media, list) and media and isinstance(content, str):
                breadcrumbs = "\n".join(
                    image_placeholder_text(p) for p in media if isinstance(p, str) and p
                )
                content = f"{content}\n{breadcrumbs}" if content else breadcrumbs
            if include_timestamps:
                content = self._annotate_message_time(message, content)
            if role == "assistant" and isinstance(content, str) and not content.strip():
                if not any(key in message for key in ("tool_calls", "reasoning_content", "thinking_blocks")):
                    continue
            entry: dict[str, Any] = {"role": message["role"], "content": content}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content", "thinking_blocks"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)

        if max_tokens > 0 and out:
            kept: list[dict[str, Any]] = []
            used = 0
            for message in reversed(out):
                tokens = estimate_message_tokens(message)
                if kept and used + tokens > max_tokens:
                    break
                kept.append(message)
                used += tokens
            kept.reverse()

            # Keep history aligned to the first visible user turn.
            first_user = next((i for i, m in enumerate(kept) if m.get("role") == "user"), None)
            if first_user is not None:
                kept = kept[first_user:]
            else:
                # Tight token budgets can otherwise leave assistant-only tails.
                # If a user turn exists in the unsliced output, recover the
                # nearest one even if it slightly exceeds the token budget.
                recovered_user = next(
                    (i for i in range(len(out) - 1, -1, -1) if out[i].get("role") == "user"),
                    None,
                )
                if recovered_user is not None:
                    kept = out[recovered_user:]

            # And keep a legal tool-call boundary at the front.
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
            out = kept
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()
        self.metadata.pop("_last_summary", None)

    def retain_recent_legal_suffix(
        self,
        max_messages: int,
        *,
        extend_to_user: bool = False,
    ) -> tuple[list[dict], int]:
        """Keep a legal recent suffix, optionally extending it back to a user turn.

        Returns ``(dropped, already_consolidated_count)`` where *dropped* is
        the list of removed messages (in original order) and
        *already_consolidated_count* is how many of those were inside the
        pre-existing ``last_consolidated`` prefix and therefore do not need
        raw archiving.
        """
        if max_messages <= 0:
            dropped = list(self.messages)
            lc = self.last_consolidated
            self.clear()
            return dropped, min(lc, len(dropped))
        if len(self.messages) <= max_messages:
            return [], 0

        original = list(self.messages)
        before_lc = self.last_consolidated

        start_idx = max(0, len(self.messages) - max_messages)
        if extend_to_user:
            start_idx = next(
                (i for i in range(start_idx, -1, -1) if self.messages[i].get("role") == "user"),
                start_idx,
            )

        retained = self.messages[start_idx:]

        # Prefer starting at a user turn when one exists within the retained window.
        first_user = next((i for i, m in enumerate(retained) if m.get("role") == "user"), None)
        if first_user is not None:
            retained = retained[first_user:]
        elif not extend_to_user:
            # If the hard-capped tail is assistant/tool-only, anchor to the
            # latest user in the full session and take a capped forward window.
            latest_user = next(
                (i for i in range(len(self.messages) - 1, -1, -1)
                 if self.messages[i].get("role") == "user"),
                None,
            )
            if latest_user is not None:
                retained = self.messages[latest_user: latest_user + max_messages]

        # Mirror get_history(): avoid persisting orphan tool results at the front.
        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        # Hard-cap guarantee unless the caller requested user-turn extension.
        if not extend_to_user and len(retained) > max_messages:
            retained = retained[-max_messages:]
            start = find_legal_message_start(retained)
            if start:
                retained = retained[start:]

        # Compute actually-dropped messages using identity comparison so that
        # even when retained is a non-contiguous slice of original (the else
        # branch above), we never duplicate or lose messages.
        retained_ids = set(id(m) for m in retained)
        dropped = [m for m in original if id(m) not in retained_ids]

        # Count how many dropped messages were in the already-consolidated
        # prefix of the original list.  This cannot be a simple min() because
        # dropped may include messages from *after* the consolidated prefix
        # (e.g. in the else branch).
        already_consolidated = sum(
            1 for i, m in enumerate(original)
            if i < before_lc and id(m) not in retained_ids
        )

        # New last_consolidated = count of retained messages that were inside
        # the old consolidated prefix.
        new_lc = sum(
            1 for i, m in enumerate(original)
            if i < before_lc and id(m) in retained_ids
        )

        self.messages = retained
        self.last_consolidated = new_lc
        self.updated_at = datetime.now()
        return dropped, already_consolidated

    def enforce_file_cap(
        self,
        on_archive: Any = None,
        limit: int = FILE_MAX_MESSAGES,
    ) -> None:
        """Bound session message growth by archiving and trimming old prefixes."""
        if limit <= 0 or len(self.messages) <= limit:
            return

        dropped, already_consolidated = self.retain_recent_legal_suffix(limit)
        if not dropped:
            return

        archive_chunk = dropped[already_consolidated:]
        if archive_chunk and on_archive:
            on_archive(archive_chunk)
        logger.info(
            "Session file cap hit for {}: dropped {}, raw-archived {}, kept {}",
            self.key,
            len(dropped),
            len(archive_chunk),
            len(self.messages),
        )


def last_user_message_ms(session: Session | None) -> int | None:
    """Epoch ms dell'ultima riga scritta **dall'utente**. ``None`` se non ce n'è.

    Serve a rispondere a "l'utente si è fatto vivo dopo che gli abbiamo parlato?"
    (v. ``jenny/cron/heartbeat_tasks.py::rearm_after_user_message``), e le tre
    scelte che la rendono affidabile sono tutte scelte su cosa NON contare:

    - **non** ``session.updated_at``: si muove anche quando è Jenny a scrivere,
      compreso il momento in cui scrive l'avviso stesso che si sta cercando di
      ricordare (``loop.py``, ``record_channel_delivery``). Serve una riga
      ``role == "user"``, non l'ultima attività della sessione.
    - **non** le righe che l'utente non ha scritto pur portandone il ruolo. Un
      job ``reminder`` si persiste nella storia esattamente come un messaggio
      dell'utente — stessa riga, stesso ruolo (``cron_history_overrides``) — e
      contarlo vorrebbe dire che un promemoria delle 09:00 "risponde" ogni
      mattina al posto di chi dorme. Lo stesso vale per il rientro di un
      subagent e per lo sprone a un sustained goal: l'elenco completo, e il
      perché, stanno in ``jenny.session.history_meta``.
    - **non** ``timestamp`` presi per buoni: la riga potrebbe non averlo, o
      averlo illeggibile. Si scorre indietro fino alla prima utilizzabile, che
      è la direzione sicura — un timbro più vecchio riarma di meno, mai di più.

    Il timbro è una ISO **naive in ora locale** (v. ``Session.add_message``):
    ``fromisoformat`` + ``timestamp()`` lo interpretano nello stesso fuso in cui
    è stato scritto, che è l'unica lettura corretta di una stringa senza offset.
    """
    if session is None:
        return None
    for message in reversed(session.messages):
        if message.get("role") != "user" or is_synthetic_history_row(message):
            continue
        raw = message.get("timestamp")
        if not isinstance(raw, str):
            continue
        try:
            return int(datetime.fromisoformat(raw).timestamp() * 1000)
        except (ValueError, OSError, OverflowError):
            continue
    return None


@dataclass
class _ParsedSessionFile:
    """Risultato grezzo di una passata sul file JSONL di sessione."""

    metadata_record: dict[str, Any] = field(default_factory=dict)
    first_record_is_metadata: bool = False
    messages: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0


def _parse_session_file(
    path: Path,
    *,
    tolerant: bool,
    first_record_only: bool = False,
) -> _ParsedSessionFile:
    """Passata unica sul file JSONL di sessione, condivisa da tutti i lettori.

    ``tolerant`` salta le righe non decodificabili contandole invece di
    propagare l'errore (path di riparazione); altrimenti un JSON malformato
    propaga al chiamante. ``first_record_only`` si ferma dopo il primo record
    non vuoto, per la lettura della sola metadata. Il record ``_type=="metadata"``
    piu' recente vince; l'estrazione dei singoli campi resta ai chiamanti perche'
    le loro politiche su date e fallback divergono.
    """
    result = _ParsedSessionFile()
    seen_record = False
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if tolerant:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    result.skipped += 1
                    continue
            else:
                data = json.loads(line)
            is_metadata = data.get("_type") == "metadata"
            if not seen_record:
                seen_record = True
                result.first_record_is_metadata = is_metadata
            if is_metadata:
                result.metadata_record = data
            else:
                result.messages.append(data)
            if first_record_only:
                break
    return result


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self._cache: dict[str, Session] = {}

    @staticmethod
    def safe_key(key: str) -> str:
        """Public helper used by HTTP handlers to map an arbitrary key to a stable filename stem."""
        return safe_filename(key.replace(":", "_"))

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)

        if not path.exists():
            return None

        try:
            parsed = _parse_session_file(path, tolerant=False)
            meta = parsed.metadata_record
            created_raw = meta.get("created_at")
            updated_raw = meta.get("updated_at")
            return Session(
                key=key,
                messages=parsed.messages,
                created_at=(
                    datetime.fromisoformat(created_raw) if created_raw else datetime.now()
                ),
                updated_at=(
                    datetime.fromisoformat(updated_raw) if updated_raw else datetime.now()
                ),
                metadata=meta.get("metadata", {}),
                last_consolidated=meta.get("last_consolidated", 0),
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered session {} from corrupt file ({} messages)", key, len(repaired.messages))
            return repaired

    def _repair(self, key: str) -> Session | None:
        """Attempt to recover a session from a corrupt JSONL file."""
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            parsed = _parse_session_file(path, tolerant=True)
            if parsed.skipped:
                logger.warning("Skipped {} corrupt lines in session {}", parsed.skipped, key)

            meta = parsed.metadata_record
            metadata = meta.get("metadata", {})
            if not parsed.messages and not metadata:
                return None

            created_at: datetime | None = None
            updated_at: datetime | None = None
            if meta.get("created_at"):
                with suppress(ValueError, TypeError):
                    created_at = datetime.fromisoformat(meta["created_at"])
            if meta.get("updated_at"):
                with suppress(ValueError, TypeError):
                    updated_at = datetime.fromisoformat(meta["updated_at"])

            return Session(
                key=key,
                messages=parsed.messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=meta.get("last_consolidated", 0),
            )
        except Exception as e:
            logger.warning("Repair failed for session {}: {}", key, e)
            return None

    @staticmethod
    def _session_payload(session: Session) -> dict[str, Any]:
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "messages": session.messages,
        }

    def save(self, session: Session, *, fsync: bool = False) -> None:
        """Save a session to disk atomically.

        When *fsync* is ``True`` the final file and its parent directory are
        explicitly flushed to durable storage.  This is intentionally off by
        default (the OS page-cache is sufficient for normal operation) but
        should be enabled during graceful shutdown so that filesystems with
        write-back caching (e.g. rclone VFS, NFS, FUSE mounts) do not lose
        the most recent writes.
        """
        path = self._get_session_path(session.key)
        metadata_line = {
            "_type": "metadata",
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated
        }
        lines = [json.dumps(metadata_line, ensure_ascii=False) + "\n"]
        for msg in session.messages:
            lines.append(json.dumps(msg, ensure_ascii=False) + "\n")
        atomic_write(path, "".join(lines), fsync_file=fsync, fsync_dir=fsync)
        self._cache[session.key] = session

    def flush_all(self) -> int:
        """Re-save every cached session with fsync for durable shutdown.

        Returns the number of sessions flushed.  Errors on individual
        sessions are logged but do not prevent other sessions from being
        flushed.
        """
        flushed = 0
        for key, session in list(self._cache.items()):
            try:
                self.save(session, fsync=True)
                flushed += 1
            except Exception:
                logger.warning("Failed to flush session {}", key, exc_info=True)
        return flushed

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """Load a session from disk without caching; intended for read-only HTTP endpoints.

        Returns ``{"key", "created_at", "updated_at", "metadata", "messages"}`` or
        ``None`` when the session file does not exist or fails to parse.
        """
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            parsed = _parse_session_file(path, tolerant=False)
            meta = parsed.metadata_record
            return {
                "key": meta.get("key") or key,
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "metadata": meta.get("metadata", {}),
                "messages": parsed.messages,
            }
        except Exception as e:
            logger.warning("Failed to read session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered read-only session view {} from corrupt file", key)
                return self._session_payload(repaired)
            return None

    def read_session_metadata(self, key: str) -> dict[str, Any] | None:
        """Load only the metadata record from a session file.

        This is used by WebUI routes that need session-level metadata but not the
        full conversation transcript.
        """
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            parsed = _parse_session_file(path, tolerant=False, first_record_only=True)
            if not parsed.first_record_is_metadata:
                return None
            meta = parsed.metadata_record
            metadata = meta.get("metadata", {})
            return {
                "key": meta.get("key") or key,
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        except Exception as e:
            logger.warning("Failed to read session metadata {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered read-only session metadata {} from corrupt file", key)
                return {
                    "key": repaired.key,
                    "created_at": repaired.created_at.isoformat(),
                    "updated_at": repaired.updated_at.isoformat(),
                    "metadata": repaired.metadata,
                }
            return None

