"""Session management for conversation history."""

import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.session import store
from jenny.session.history_meta import (
    CHECKPOINT_META,
    COMPACTED_META,
    REASONING_DROPPED_META,
    SENT_CONTENT_META,
)
from jenny.utils.helpers import (
    channel_delivery_aware_user_start,
    estimate_message_tokens,
    find_legal_message_start,
    image_placeholder_text,
    merge_message_content,
    recent_message_start_index,
    safe_filename,
)

FILE_MAX_MESSAGES = 2000

# Nei metadata di una sessione-progetto: quanti dei suoi messaggi, dall'inizio,
# la raccolta del diario ha gia' riassunto (``AutoCompact._harvest_project_diary``).
# Sta qui e non nell'autocompact perche' e' un indice nei messaggi, e chi li
# accorcia o li azzera — :meth:`Session.clear`, :meth:`Session.retain_recent_legal_suffix`
# — deve spostarlo insieme a loro, come fa con ``last_consolidated``.
DIARY_HARVEST_METADATA_KEY = "_diary_harvested"
_MESSAGE_TIME_PREFIX_RE = re.compile(r"^\[Message Time: [^\]]+\]\n?")
_LOCAL_IMAGE_BREADCRUMB_RE = re.compile(r"^\[image: (?:/|~)[^\]]+\]\s*$")
_TOOL_CALL_ECHO_RE = re.compile(r'^\s*message\([^)]*\)\s*$')
# Un code point surrogato in una ``str`` e' sempre isolato: una coppia valida,
# decodificata, e' gia' un carattere solo. In UTF-8 non esiste, e ``json.loads``
# lo produce da un frame tagliato a meta' di un'emoji (``"\\ud83d"``).
_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def scrub_lone_surrogates(text: str) -> str:
    """*text* con ogni surrogato isolato sostituito da U+FFFD.

    Un surrogato in un messaggio faceva fallire **ogni** salvataggio della
    sessione da li' in poi — ``encode("utf-8")`` lo rifiuta — e anche i turni
    puliti rispondevano con l'errore generico fino al riavvio. Si ripulisce
    all'ingresso del messaggio (``AgentLoop._process_message``) e, come rete, in
    :meth:`SessionManager.save`.
    """
    return _LONE_SURROGATE_RE.sub("\ufffd", text)


def _scrub_value(value: Any) -> Any:
    """:func:`scrub_lone_surrogates` applicata a ogni stringa di una struttura JSON."""
    if isinstance(value, str):
        return scrub_lone_surrogates(value)
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, dict):
        return {_scrub_value(k): _scrub_value(v) for k, v in value.items()}
    return value


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
        replay_sent: bool = False,
    ) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input.

        History is sliced by message count first (``max_messages``), then by
        token budget from the tail (``max_tokens``) when provided.

        Con *replay_sent* una riga che porta il testo com'e' partito
        (``SENT_CONTENT_META``) torna con quello, senza ``[Message Time]`` e
        senza breadcrumb: e' la richiesta del turno dopo, e deve rimandare il
        prefisso che il provider ha gia' in cache. Lo chiedono solo i due
        costruttori del turno; il consolidatore e ``/history`` leggono
        ``content``.
        """
        # Le righe sostituite dal checkpoint di un turno lungo non tornano: al
        # loro posto, in coda alla richiesta, il checkpoint (``CHECKPOINT_META``).
        unconsolidated = [
            m for m in self.messages[self.last_consolidated:] if not m.get(COMPACTED_META)
        ]
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
            checkpoint = message.get(CHECKPOINT_META)
            if isinstance(checkpoint, str) and checkpoint:
                if out and out[-1].get("role") == "user":
                    out[-1] = {
                        **out[-1],
                        "content": merge_message_content(out[-1].get("content"), checkpoint),
                    }
                else:
                    out.append({"role": "user", "content": checkpoint})
            content = message.get("content", "")
            role = message.get("role")
            sent = message.get(SENT_CONTENT_META) if replay_sent else None
            if isinstance(sent, str):
                replayed: dict[str, Any] = {"role": role, "content": sent}
                for key in ("tool_calls", "tool_call_id", "name"):
                    if key in message:
                        replayed[key] = message[key]
                out.append(replayed)
                continue
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
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content", "thinking_blocks",
                        REASONING_DROPPED_META):
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
        # L'indice della raccolta del diario conta messaggi che non ci sono piu':
        # tenuto, dopo ``/new`` i messaggi nuovi — meno dei vecchi — non
        # sarebbero mai stati raccolti.
        self.metadata.pop(DIARY_HARVEST_METADATA_KEY, None)

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

        # L'indice della raccolta del diario scorre allo stesso modo: resta il
        # numero dei messaggi *rimasti* che erano gia' stati raccolti.
        harvested = self.metadata.get(DIARY_HARVEST_METADATA_KEY)
        if isinstance(harvested, int) and not isinstance(harvested, bool):
            self.metadata[DIARY_HARVEST_METADATA_KEY] = sum(
                1 for i, m in enumerate(original)
                if i < harvested and id(m) in retained_ids
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


class SessionManager:
    """Le conversazioni, righe di ``state.sqlite`` (``session.store``).

    Una sessione si carica una volta e resta in cache; un salvataggio scrive
    solo i messaggi cambiati o nuovi, in una transazione: una conversazione da
    duemila messaggi non si riscrive più intera a ogni checkpoint.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._cache: dict[str, Session] = {}
        # Per chiave: (versione, impronte dei messaggi) come li ha salvati o
        # letti questa istanza. ``save`` scrive solo quel che ne differisce.
        self._saved: dict[str, tuple[int, list[int]]] = {}

    @property
    def _db(self):
        return store.session_database(self.workspace)

    @staticmethod
    def safe_key(key: str) -> str:
        """*key* in forma di nome di file (i verbali della WebUI sono ancora file)."""
        return safe_filename(key.replace(":", "_"))

    def get_or_create(self, key: str) -> Session:
        """La sessione *key*, dalla cache o dal database, o una nuova vuota."""
        if key in self._cache:
            return self._cache[key]
        session = self._load(key) or Session(key=key)
        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        loaded = store.load(self._db, key)
        if loaded is None:
            return None
        self._saved[key] = (loaded["version"], loaded["known"])
        return Session(
            key=key,
            messages=loaded["messages"],
            created_at=_when(loaded["created_at"]),
            updated_at=_when(loaded["updated_at"]),
            metadata=loaded["metadata"],
            last_consolidated=loaded["last_consolidated"],
        )

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
        """Salva *session*: i metadati e i messaggi cambiati, in una transazione.

        *fsync* resta per i chiamanti dello spegnimento: una transazione chiusa
        è già sul disco quanto basta (``synchronous=NORMAL`` in WAL).
        """
        try:
            self._write(session)
        except UnicodeEncodeError:
            # Un surrogato isolato arrivato da una porta che non ripulisce: si
            # ripulisce la sessione **in memoria**, cosi' cache e database restano
            # uguali, e si riscrive. Senza, ogni salvataggio successivo falliva.
            logger.warning(
                "Session {} carried a lone UTF-16 surrogate; replaced with U+FFFD", session.key,
            )
            session.messages = _scrub_value(session.messages)
            session.metadata = _scrub_value(session.metadata)
            self._write(session)
        self._cache[session.key] = session

    def _write(self, session: Session) -> None:
        lines = [json.dumps(m, ensure_ascii=False) for m in session.messages]
        version, known = self._saved.get(session.key, (None, None))
        saved = store.save(
            self._db, session.key,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            metadata=json.dumps(session.metadata, ensure_ascii=False),
            last_consolidated=session.last_consolidated,
            lines=lines, known=known, known_version=version,
        )
        self._saved[session.key] = saved

    def flush_all(self) -> int:
        """Risalva ogni sessione in cache, per uno spegnimento ordinato.

        Una sessione **vuota e mai salvata** non si scrive: è quella che
        ``get_or_create`` inventa per chi ha solo guardato — l'inseguimento di
        un progetto rinominato, il controllo sull'id della wiki — e che resta in
        cache dopo che il rinomino o la cancellazione l'aveva invalidata.
        Scriverla allo spegnimento faceva risorgere la sessione del nome
        vecchio, e bastava a rifiutare un rinomino all'indietro con
        ``name_taken``.
        """
        flushed = 0
        for key, session in list(self._cache.items()):
            try:
                if not session.messages and not session.metadata and not self.exists(key):
                    continue
                self.save(session, fsync=True)
                flushed += 1
            except Exception:
                logger.opt(exception=True).warning("Failed to flush session {}", key)
        return flushed

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)
        self._saved.pop(key, None)

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """La sessione senza metterla in cache, per le rotte in sola lettura.

        ``{"key", "created_at", "updated_at", "metadata", "messages"}``, o ``None``.
        """
        loaded = store.load(self._db, key)
        if loaded is None:
            return None
        return {k: loaded[k] for k in ("key", "created_at", "updated_at", "metadata",
                                       "messages")}

    def read_session_metadata(self, key: str) -> dict[str, Any] | None:
        """Solo i metadati della sessione (senza i messaggi), o ``None``."""
        found = store.head(self._db, key)
        if found is None:
            return None
        return {k: found[k] for k in ("key", "created_at", "updated_at", "metadata")}

    # -- elenchi e ciclo di vita (chi prima guardava i file) ----------------

    def exists(self, key: str) -> bool:
        return store.head(self._db, key) is not None

    def stats(self, key: str) -> tuple[int, int] | None:
        """``(messaggi, byte)`` della sessione salvata, o ``None``."""
        found = store.head(self._db, key)
        return None if found is None else (found["count"], found["bytes"])

    def list_keys(self, prefix: str = "") -> list[str]:
        """Le chiavi salvate che cominciano con *prefix*, dalla meno recente."""
        return store.keys(self._db, prefix)

    def delete(self, key: str) -> bool:
        """Toglie la sessione salvata e la dimentica. True se c'era."""
        self.invalidate(key)
        return store.delete(self._db, key)

    # -- il diario del turno in corso (``TurnPersistenceMixin``) --------------

    def turn_reset(self, key: str, stamp: str, lines: list[str]) -> None:
        store.turn_reset(self._db, key, stamp, lines)

    def turn_append(self, key: str, start: int, lines: list[str]) -> None:
        store.turn_append(self._db, key, start, lines)

    def turn_read(self, key: str) -> tuple[str | None, list[str]]:
        return store.turn_read(self._db, key)

    def turn_drop(self, key: str) -> None:
        store.turn_drop(self._db, key)


def _when(raw: Any) -> datetime:
    if isinstance(raw, str) and raw:
        with suppress(ValueError):
            return datetime.fromisoformat(raw)
    return datetime.now()
