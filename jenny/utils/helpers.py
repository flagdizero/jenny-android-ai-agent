"""Utility functions for jenny."""

import base64
import json
import re
import shutil
import time
from contextlib import suppress
from datetime import datetime, tzinfo
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from jenny.utils.path import atomic_write

# DeepSeek-family special tokens are delimited by FULLWIDTH VERTICAL LINE
# (U+FF5C), not the ASCII pipe, and separate words with U+2581: e.g.
# `<｜end▁of▁thinking｜>`, `<｜Assistant｜>`, `<｜tool▁calls▁begin｜>`.
_DEEPSEEK_TOKEN = r"<｜{1,2}[^<>\n]{0,48}?｜{1,2}>"
# DSML (DeepSeek Markup Language) is the tool-call markup of the deepseek-v4
# template: `<｜｜DSML｜｜tool_calls>`, `<｜｜DSML｜｜invoke name="...">`. Unlike the
# markers above it OPENS a block, so what follows it is machinery, not prose.
_DSML_BLOCK_OPENER = r"<｜｜DSML｜｜"


def strip_think(text: str) -> str:
    """Remove thinking blocks, unclosed trailing tags, and tokenizer-level
    template leaks occasionally emitted by local model endpoints.

    Covers:
      1. Well-formed `<think>...</think>` and `<thought>...</thought>` blocks.
      2. Streaming prefixes where the block is never closed.
      3. *Malformed* opening tags missing the `>` — e.g. `<think广场…`. The
         model sometimes emits the tag name directly followed by user-facing
         content with no delimiter; without this step the literal `<think`
         leaks into the rendered message.
      4. Harmony-style channel markers like `<channel|>` / `<|channel|>`
         **at the start of the text** — conservative to avoid eating
         explanatory prose that mentions these tokens.
      5. Orphan closing tags `</think>` / `</thought>` **at the very start
         or end of the text** only, for the same reason.
      6. Trailing partial control tags split across stream chunks, such as
         `<thi`, `<thin`, or `<tho`.
      7. DeepSeek-family special tokens (`<｜end▁of▁thinking｜>`,
         `<｜Assistant｜>`, `<｜tool▁calls▁begin｜>`) **at the edges** of the
         text, same conservatism as (4)/(5).
      8. A DSML tool-call block (`<｜｜DSML｜｜tool_calls>`) opened **at the
         start**: everything from the opener to the end of the text goes,
         because an opened block is machinery all the way down — leaving it
         half-stripped would surface `invoke name="..."` to the user.

    Since this is also applied before persisting to history (memory.py),
    the edge-only stripping of (4), (5) and (7)/(8) is deliberate: stripping
    those tokens mid-text would silently rewrite any message where a user or
    the assistant discusses the tokens themselves.
    """
    # Well-formed blocks first.
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"^\s*<think>[\s\S]*$", "", text)
    text = re.sub(r"<thought>[\s\S]*?</thought>", "", text)
    text = re.sub(r"^\s*<thought>[\s\S]*$", "", text)
    # Malformed opening tags: `<think` / `<thought` where the next char is
    # NOT one that could continue a valid tag / identifier name. Explicitly
    # listing ASCII tag-name chars (letters, digits, `_`, `-`, `:`) plus
    # `>` / `/` — we can't use `\w` here because in Python's default
    # Unicode regex mode it matches CJK characters too, which would defeat
    # the primary fix for `<think广场…` leaks.
    text = re.sub(r"<think(?![A-Za-z0-9_\-:>/])", "", text)
    text = re.sub(r"<thought(?![A-Za-z0-9_\-:>/])", "", text)
    # Edge-only orphan closing tags (start or end of text).
    text = re.sub(r"^\s*</think>\s*", "", text)
    text = re.sub(r"\s*</think>\s*$", "", text)
    text = re.sub(r"^\s*</thought>\s*", "", text)
    text = re.sub(r"\s*</thought>\s*$", "", text)
    # Edge-only channel markers (harmony / Gemma 4 variant leaks).
    text = re.sub(r"^\s*<\|?channel\|?>\s*", "", text)
    # Stream chunks may end in the middle of a control tag. Strip only known
    # control-token prefixes at the very end.
    partial_control_tag = (
        r"</?(?:t|th|thi|thin|think|tho|thou|thoug|though|thought)>?"
        r"|<\|?(?:c|ch|cha|chan|chann|channe|channel)(?:\|?>?)?"
    )
    text = re.sub(rf"(?:{partial_control_tag})$", "", text)
    text = re.sub(r"^\s*<\|?$", "", text)
    # DeepSeek template leaks. Measured on the device 2026-08-26 23:13: a
    # heartbeat turn delivered `<｜end▁of▁thinking｜>\n\n<｜｜DSML｜｜tool_calls>\n
    # <｜｜DSML｜｜invoke name="complete_goal">…` as a proactive alert — the model
    # wrote its own tool-call markup as plain text (truncated mid-parameter, so
    # the endpoint's DSML parser never recognised it) and it reached the chat.
    # The leading marker comes first, so the loop peels markers until the DSML
    # opener (if any) is the head of the text and can be cut to the end.
    while True:
        cleaned = re.sub(rf"^\s*(?:{_DEEPSEEK_TOKEN})", "", text)
        cleaned = re.sub(rf"^\s*{_DSML_BLOCK_OPENER}[\s\S]*$", "", cleaned)
        if cleaned == text:
            break
        text = cleaned
    # Tail end: a complete marker after real text, or one cut in half by a
    # stream boundary (`answer <｜end▁of`), same shape as the ASCII rule above.
    text = re.sub(rf"\s*(?:{_DEEPSEEK_TOKEN})\s*$", "", text)
    text = re.sub(r"\s*<｜{1,2}[^<>\n]{0,48}$", "", text)
    return text.strip()


def extract_think(text: str) -> tuple[str | None, str]:
    """Extract thinking content from inline ``<think>`` / ``<thought>`` blocks.

    Returns ``(thinking_text, cleaned_text)``. Only closed blocks are
    extracted; unclosed streaming prefixes are stripped from the cleaned
    text but not surfaced — :func:`strip_think` handles that case.
    """
    parts: list[str] = []
    for m in re.finditer(r"<think>([\s\S]*?)</think>", text):
        parts.append(m.group(1).strip())
    for m in re.finditer(r"<thought>([\s\S]*?)</thought>", text):
        parts.append(m.group(1).strip())
    thinking = "\n\n".join(parts) if parts else None
    return thinking, strip_think(text)


class IncrementalThinkExtractor:
    """Stateful inline ``<think>`` extractor for streaming buffers.

    Streaming providers expose only a single content delta channel. When a
    model embeds reasoning in ``<think>...</think>`` blocks inside that
    channel, callers need to surface the reasoning incrementally as it
    arrives without re-emitting earlier text. This holds the "already
    emitted" cursor so the runner and the loop hook share one shape.
    """

    __slots__ = ("_emitted",)

    def __init__(self) -> None:
        self._emitted = ""

    def reset(self) -> None:
        self._emitted = ""

    async def feed(self, buf: str, emit: Any) -> bool:
        """Emit any new thinking text found in ``buf``.

        Returns True if anything was emitted this call. ``emit`` is an
        async callable taking a single string (typically
        ``hook.emit_reasoning``).
        """
        thinking, _ = extract_think(buf)
        if not thinking or thinking == self._emitted:
            return False
        new = thinking[len(self._emitted):].strip()
        self._emitted = thinking
        if not new:
            return False
        await emit(new)
        return True


class IncrementalAnswerStepper:
    """Stepper condiviso per lo split incrementale think/answer in streaming.

    Accumula il buffer grezzo, estrae il reasoning inline (``<think>``) via
    ``IncrementalThinkExtractor`` e ritorna la sola porzione incrementale del
    testo-risposta già ripulito. È l'unica implementazione dello slice + feed
    usata sia dall'hook di progress sia dal percorso ``_stream_progress`` del
    runner: l'orchestrazione del flag ``reasoning_open`` resta nel chiamante.
    """

    __slots__ = ("_buf", "_extractor")

    def __init__(self) -> None:
        self._buf = ""
        self._extractor = IncrementalThinkExtractor()

    def reset(self) -> None:
        self._buf = ""
        self._extractor.reset()

    async def feed(self, delta: str, emit_reasoning: Any) -> tuple[str, bool]:
        """Consuma un delta di streaming.

        Emette il reasoning nuovo tramite ``emit_reasoning`` e ritorna
        ``(incremental, emitted_reasoning)``: ``incremental`` è il testo-risposta
        nuovo (senza ``<think>``), ``emitted_reasoning`` indica se in questo step
        è stato emesso del reasoning.
        """
        prev_clean = strip_think(self._buf)
        self._buf += delta
        new_clean = strip_think(self._buf)
        incremental = new_clean[len(prev_clean):]
        emitted = await self._extractor.feed(self._buf, emit_reasoning)
        return incremental, emitted


def extract_reasoning(
    reasoning_content: str | None,
    thinking_blocks: list[dict[str, Any]] | None,
    content: str | None,
) -> tuple[str | None, str | None]:
    """Return ``(reasoning_text, cleaned_content)`` from one model response.

    Single source of truth for "what reasoning did this response carry, and
    what answer text remains after we peel it out". Fallback order:

    1. Dedicated ``reasoning_content`` (DeepSeek-R1, Kimi, MiMo, OpenAI
       reasoning models, Bedrock).
    2. Anthropic ``thinking_blocks``.
    3. Inline ``<think>`` / ``<thought>`` blocks in ``content``.

    Only one source contributes per response; lower-priority sources are
    ignored if a higher-priority one is present, but inline ``<think>``
    tags are still stripped from ``content`` so they never leak into the
    final answer.
    """
    if reasoning_content:
        return reasoning_content, strip_think(content) if content else content
    if thinking_blocks:
        parts = [
            tb.get("thinking", "")
            for tb in thinking_blocks
            if isinstance(tb, dict) and tb.get("type") == "thinking"
        ]
        joined = "\n\n".join(p for p in parts if p)
        return (joined or None), strip_think(content) if content else content
    if content:
        return extract_think(content)
    return None, content


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes, ignoring file extension."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def build_image_content_blocks(
    raw: bytes, mime: str, path: str, label: str
) -> list[dict[str, Any]]:
    """Build native image blocks plus a short text label."""
    b64 = base64.b64encode(raw).decode()
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
            "_meta": {"path": path},
        },
        {"type": "text", "text": label},
    ]


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def current_time_str(timezone: str | None = None) -> str:
    """Return the current time string."""
    tz = safe_zoneinfo(timezone) if timezone else None
    now = datetime.now(tz=tz) if tz else datetime.now().astimezone()
    offset = now.strftime("%z")
    offset_fmt = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    tz_name = timezone or (time.strftime("%Z") or "UTC")
    return f"{now.strftime('%Y-%m-%d %H:%M (%A)')} ({tz_name}, UTC{offset_fmt})"


def safe_zoneinfo(timezone: str) -> tzinfo:
    """Ritorna un ``tzinfo`` per il nome dato, senza mai sollevare.

    Su Android/Chaquopy il database tzdata può mancare del tutto: in quel
    caso persino ``ZoneInfo("UTC")`` solleva, quindi il fallback usa
    l'offset locale corrente della libc (corretto per le datetime naive
    locali ma fisso: in modalità degradata non segue le transizioni DST)
    e, come ultima risorsa, UTC.
    """
    try:
        return ZoneInfo(timezone)
    except Exception:
        pass
    try:
        local = datetime.now().astimezone().tzinfo
        if local is not None:
            return local
    except Exception:
        pass
    return dt_timezone.utc


def tzdata_available() -> bool:
    """True se il database tzdata è utilizzabile in questo ambiente."""
    try:
        ZoneInfo("UTC")
        return True
    except Exception:
        return False


def validate_timezone_name(tz: str) -> str | None:
    """Valida un nome IANA; ritorna un messaggio d'errore oppure ``None``.

    Se il database tzdata manca del tutto (Android senza il wheel
    ``tzdata``) accetta qualsiasi nome invece di bloccare: meglio degradare
    che rendere inutilizzabile il cron.
    """
    if not tzdata_available():
        logger.warning(
            "Timezone database unavailable; accepting timezone '{}' without validation", tz
        )
        return None
    try:
        ZoneInfo(tz)
    except Exception:
        return f"unknown timezone '{tz}'"
    return None


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_TOOL_RESULT_PREVIEW_CHARS = 1200
_TOOL_RESULTS_DIR = ".jenny/tool-results"
_TOOL_RESULT_RETENTION_SECS = 7 * 24 * 60 * 60
_TOOL_RESULT_MAX_BUCKETS = 32
_TRUNCATED_SUFFIX = "\n... (truncated)"


def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def image_placeholder_text(path: str | None, *, empty: str = "[image]") -> str:
    """Build an image placeholder string."""
    return f"[image: {path}]" if path else empty


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text with a stable suffix."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + _TRUNCATED_SUFFIX


# Caratteri per token nelle stime a occhio. **Una** costante perché le stime
# devono essere d'accordo fra loro: il consolidator sottrae dal budget quello
# che il troncatore qui sotto applica, e due convenzioni diverse si
# manifesterebbero come una richiesta fuori finestra invece che come un taglio.
CHARS_PER_TOKEN = 4


def truncate_head_tail(text: str, max_chars: int) -> tuple[str, int]:
    """*text* entro *max_chars*, tenendo metà in testa e metà in coda.

    Ritorna il testo e quanti caratteri sono spariti (0 se ci stava). In mezzo
    resta la riga che lo dice. Era copiato in ``python_exec`` e nelle sue
    sessioni.
    """
    if len(text) <= max_chars:
        return text, 0
    half = max_chars // 2
    cut = len(text) - max_chars
    return text[:half] + f"\n\n... ({cut:,} chars truncated) ...\n\n" + text[-half:], cut


def truncate_text_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to a token budget with a stable suffix.

    Uses a character-based estimate (``CHARS_PER_TOKEN``).
    """
    if max_tokens <= 0:
        return text
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


def recent_message_start_index(
    messages: list[dict[str, Any]],
    max_messages: int,
    *,
    extend_to_user: bool = False,
) -> int:
    """Return the start index for a recent replay window."""
    if max_messages <= 0:
        return len(messages)
    start_idx = max(0, len(messages) - max_messages)
    if not extend_to_user or len(messages) <= max_messages:
        return start_idx
    if any(messages[i].get("role") == "user" for i in range(start_idx, len(messages))):
        return start_idx

    recovered_user = next(
        (i for i in range(start_idx - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if recovered_user is None:
        return start_idx
    if recovered_user > 0 and messages[recovered_user - 1].get("_channel_delivery"):
        return recovered_user - 1
    return recovered_user


def channel_delivery_aware_user_start(messages: list[dict[str, Any]]) -> int | None:
    """Indice del primo messaggio ``user`` inglobando l'eventuale consegna proattiva.

    Se il messaggio che precede il primo ``user`` porta il marker
    ``_channel_delivery`` (consegna assistant proattiva a cui l'utente può stare
    rispondendo) arretra di uno. Ritorna ``None`` se non esiste alcun ``user``
    (in tal caso il chiamante non deve tagliare).
    """
    for i, message in enumerate(messages):
        if message.get("role") == "user":
            if i > 0 and messages[i - 1].get("_channel_delivery"):
                return i - 1
            return i
    return None


def merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
    """Unisce due content di messaggio (stringa o lista di blocchi)."""
    if isinstance(left, str) and isinstance(right, str):
        return f"{left}\n\n{right}" if left else right

    def _to_blocks(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [
                item if isinstance(item, dict) else {"type": "text", "text": str(item)}
                for item in value
            ]
        if value is None:
            return []
        return [{"type": "text", "text": str(value)}]

    return _to_blocks(left) + _to_blocks(right)


def find_legal_message_start(messages: list[dict[str, Any]]) -> int:
    """Find the first index whose tool results have matching assistant calls."""
    declared: set[str] = set()
    start = 0
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    declared.add(str(tc["id"]))
        elif role == "tool":
            tid = msg.get("tool_call_id")
            if tid and str(tid) not in declared:
                start = i + 1
                declared.clear()
    return start


def stringify_text_blocks(content: list[dict[str, Any]]) -> str | None:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            return None
        if block.get("type") != "text":
            return None
        text = block.get("text")
        if not isinstance(text, str):
            return None
        parts.append(text)
    return "\n".join(parts)


def _render_tool_result_reference(
    filepath: Path,
    *,
    original_size: int,
    preview: str,
    truncated_preview: bool,
    total_lines: int,
    next_offset: int | None = None,
) -> str:
    """Riferimento a un output tool spillato su file.

    Il riferimento invita a rileggere il file, e ``read_file`` pagina per RIGA:
    dire solo quanti caratteri sono stati salvati costringeva il modello a
    inventarsi un ``offset``, e un offset oltre la fine torna
    "Error: offset N is beyond end of file" — un errore tool a tutti gli effetti,
    speso per una domanda a cui questo testo poteva rispondere. Quindi porta il
    conteggio delle righe e, quando la preview e tagliata, la riga esatta da cui
    riprendere.
    """
    result = (
        f"[tool output persisted]\n"
        f"Full output saved to: {filepath}\n"
        f"Original size: {original_size} chars, {total_lines} lines\n"
        f"Preview:\n{preview}"
    )
    if truncated_preview:
        if next_offset is not None and next_offset <= total_lines:
            result += (
                f"\n...\n(Preview cut. read_file(path, offset={next_offset}) resumes "
                f"where it stops; the file has {total_lines} lines.)"
            )
        else:
            result += (
                f"\n...\n(Preview cut. Read the saved file for the full output; "
                f"it has {total_lines} lines.)"
            )
    return result


def _bucket_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _cleanup_tool_result_buckets(root: Path, current_bucket: Path) -> None:
    siblings = [path for path in root.iterdir() if path.is_dir() and path != current_bucket]
    cutoff = time.time() - _TOOL_RESULT_RETENTION_SECS
    for path in siblings:
        if _bucket_mtime(path) < cutoff:
            shutil.rmtree(path, ignore_errors=True)
    keep = max(_TOOL_RESULT_MAX_BUCKETS - 1, 0)
    siblings = [path for path in siblings if path.exists()]
    if len(siblings) <= keep:
        return
    siblings.sort(key=_bucket_mtime, reverse=True)
    for path in siblings[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def maybe_persist_tool_result(
    workspace: Path | None,
    session_key: str | None,
    tool_call_id: str,
    content: Any,
    *,
    max_chars: int,
) -> Any:
    """Persist oversized tool output and replace it with a stable reference string."""
    if workspace is None or max_chars <= 0:
        return content

    text_payload: str | None = None
    suffix = "txt"
    if isinstance(content, str):
        text_payload = content
    elif isinstance(content, list):
        text_payload = stringify_text_blocks(content)
        if text_payload is None:
            return content
        suffix = "json"
    else:
        return content

    if len(text_payload) <= max_chars:
        return content

    root = ensure_dir(workspace / _TOOL_RESULTS_DIR)
    bucket = ensure_dir(root / safe_filename(session_key or "default"))
    try:
        _cleanup_tool_result_buckets(root, bucket)
    except Exception:
        logger.exception("Failed to clean stale tool result buckets in {}", root)
    path = bucket / f"{safe_filename(tool_call_id)}.{suffix}"
    # Il conteggio righe deve descrivere il file SCRITTO, non il payload: nel
    # caso JSON su disco finisce la serializzazione indentata dei blocchi, che ha
    # righe diverse dal testo concatenato della preview. Per lo stesso motivo la
    # riga da cui riprendere ha senso solo quando i due coincidono.
    reserialized = suffix == "json" and isinstance(content, list)
    stored = (
        json.dumps(content, ensure_ascii=False, indent=2) if reserialized else text_payload
    )
    if not path.exists():
        atomic_write(path, stored)

    preview = text_payload[:_TOOL_RESULT_PREVIEW_CHARS]
    # Righe *complete* nella preview: ogni "\n" ne chiude una. L'offset punta
    # quindi all'ultima riga mostrata a meta, che va riletta per intero.
    next_offset = None if reserialized else preview.count("\n") + 1
    return _render_tool_result_reference(
        path,
        original_size=len(text_payload),
        preview=preview,
        truncated_preview=len(text_payload) > _TOOL_RESULT_PREVIEW_CHARS,
        total_lines=len(stored.splitlines()),
        next_offset=next_offset,
    )


def build_assistant_message(
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
    thinking_blocks: list[dict] | None = None,
) -> dict[str, Any]:
    """Build a provider-safe assistant message with optional reasoning fields."""
    msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content is not None or thinking_blocks:
        msg["reasoning_content"] = reasoning_content if reasoning_content is not None else ""
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
    return msg


def estimate_prompt_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Estimate prompt tokens with a character heuristic."""
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    if tools:
        total_chars += len(json.dumps(tools, ensure_ascii=False))
    return total_chars // CHARS_PER_TOKEN + len(messages) * 4


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate prompt tokens contributed by one persisted message."""
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    parts.append(text)
            else:
                parts.append(json.dumps(part, ensure_ascii=False))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))

    for key in ("name", "tool_call_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    if message.get("tool_calls"):
        parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))

    rc = message.get("reasoning_content")
    if isinstance(rc, str) and rc:
        parts.append(rc)

    payload = "\n".join(parts)
    if not payload:
        return 4
    return max(4, len(payload) // CHARS_PER_TOKEN + 4)


# Token di output riservati di default quando il provider non specifica altro.
DEFAULT_RESERVED_OUTPUT_TOKENS = 4096
# Margine di sicurezza sul budget di contesto (stima dello spazio di output).
CONTEXT_BUDGET_SAFETY_BUFFER = 1024


def reserved_output_tokens(provider: Any, spec_max_tokens: Any = None) -> int:
    """Token di output da riservare nel budget di contesto.

    Preferisce l'override intero dello spec, poi il ``max_tokens`` di
    generazione del provider, con fallback a ``DEFAULT_RESERVED_OUTPUT_TOKENS``.
    """
    if isinstance(spec_max_tokens, int):
        return spec_max_tokens
    provider_max = getattr(getattr(provider, "generation", None), "max_tokens", None)
    if isinstance(provider_max, int):
        return provider_max
    return DEFAULT_RESERVED_OUTPUT_TOKENS


def estimate_prompt_tokens_chain(
    provider: Any,
    model: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    """Estimate prompt tokens via provider counter first, then char heuristic fallback."""
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        with suppress(Exception):
            tokens, source = provider_counter(messages, tools, model)
            if isinstance(tokens, (int, float)) and tokens > 0:
                return int(tokens), str(source or "provider_counter")
    estimated = estimate_prompt_tokens(messages, tools)
    if estimated > 0:
        return int(estimated), "heuristic"
    return 0, "none"


def build_status_content(
    *,
    version: str,
    model: str,
    start_time: float,
    last_usage: dict[str, int],
    context_window_tokens: int,
    session_msg_count: int,
    context_tokens_estimate: int,
    active_task_count: int = 0,
    max_completion_tokens: int = 8192,
) -> str:
    """Build a human-readable runtime status snapshot."""
    uptime_s = int(time.time() - start_time)
    uptime = (
        f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m"
        if uptime_s >= 3600
        else f"{uptime_s // 60}m {uptime_s % 60}s"
    )
    last_in = last_usage.get("prompt_tokens", 0)
    last_out = last_usage.get("completion_tokens", 0)
    cached = last_usage.get("cached_tokens", 0)
    ctx_total = max(context_window_tokens, 0)
    # Budget mirrors Consolidator formula: ctx_window - max_completion - _SAFETY_BUFFER
    ctx_budget = max(ctx_total - int(max_completion_tokens) - 1024, 1)
    ctx_pct = min(int((context_tokens_estimate / ctx_budget) * 100), 999) if ctx_budget > 0 else 0
    ctx_used_str = (
        f"{context_tokens_estimate // 1000}k"
        if context_tokens_estimate >= 1000
        else str(context_tokens_estimate)
    )
    ctx_total_str = f"{ctx_total // 1000}k" if ctx_total > 0 else "n/a"
    token_line = f"\U0001f4ca Tokens: {last_in} in / {last_out} out"
    if cached and last_in:
        token_line += f" ({cached * 100 // last_in}% cached)"
    lines = [
        f"\U0001f408 jenny v{version}",
        f"\U0001f9e0 Model: {model}",
        token_line,
        f"\U0001f4da Context: {ctx_used_str}/{ctx_total_str} ({ctx_pct}% of input budget)",
        f"\U0001f4ac Session: {session_msg_count} messages",
        f"\u23f1 Uptime: {uptime}",
        f"\u26a1 Tasks: {active_task_count} active",
    ]
    return "\n".join(lines)


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Sync bundled templates/skills/UI to workspace via package extraction."""
    from jenny.utils.android_assets import extract_package_dir

    added: list[str] = []

    # I template si dividono per proprietario, e ogni metà ha la sua politica.
    #
    # Quelli dell'utente (AGENTS.md, SOUL.md, USER.md, HEARTBEAT.md, MEMORY.md)
    # si creano una volta e non si toccano più: SOUL e USER li riscrive Dream,
    # gli altri l'utente, e la copia del pacchetto è solo un punto di partenza.
    #
    # I prompt di sistema (agent/**) sono invece codice: nessuno li edita a mano,
    # e riscriverli a ogni avvio è ciò che fa arrivare una correzione. Erano
    # trattati come i primi, e la conseguenza si è vista in produzione — un
    # telefono aggiornato per mesi girava ancora con i prompt della versione in
    # cui era stato installato, perché un file nuovo veniva estratto e uno
    # corretto no.
    #
    # In mezzo c'è un terzo caso, l'unico in cui si scrive dentro un file
    # dell'utente: un file che è ancora, byte per byte, una versione *nostra*
    # ritirata. Lì dentro non c'è niente dell'utente da salvare, e lasciarcelo
    # significa aspettare che ci aggiunga una riga sua — a quel punto il testo
    # ritirato diventa suo, per sempre. Gira per prima perché l'estrazione
    # ``skip_existing`` qui sotto vedrebbe comunque un file esistente e passerebbe
    # oltre: le tre politiche si leggono nell'ordine in cui sono descritte.
    from jenny.utils.android_assets import (
        _SYSTEM_PROMPT_TEMPLATES,
        _USER_OWNED_TEMPLATES,
        retire_withdrawn_templates,
    )

    # Non fatale, ed è l'unica delle tre politiche che possa permetterselo. È
    # anche la sola che scriva con ``atomic_write``, cioè senza le difese che
    # ``extract_package_dir`` si è dovuto dare (``_write_bytes_force``, nato
    # perché una scrittura fallita al boot "manderebbe in crash-loop il gateway"):
    # un ``AGENTS.md`` non scrivibile alzerebbe ``PermissionError`` da qui.
    #
    # Su un solo dei due percorsi di avvio quell'eccezione è raccolta
    # (``android_entry``, non ``runtime/container``), e lì porterebbe via con sé
    # tutto il resto della sync — compreso il refresh di ``agent/**``, cioè
    # l'unico meccanismo per cui una correzione a un prompt di sistema arriva su
    # un telefono già installato. La gerarchia è quella: ritirare del testo
    # vecchio è un'ottimizzazione, aggiornare i prompt no.
    try:
        retire_withdrawn_templates(workspace)
    except Exception:
        logger.opt(exception=True).error(
            "Could not retire withdrawn templates in {} — the withdrawn text stays "
            "on disk; continuing with the rest of the sync",
            workspace,
        )
    extract_package_dir(
        "jenny.templates", workspace, skip_existing=True, only=_USER_OWNED_TEMPLATES,
    )
    n_prompts = extract_package_dir(
        "jenny.templates", workspace, only=_SYSTEM_PROMPT_TEMPLATES,
    )
    added.append(f"agent prompts ({n_prompts} files)")

    # Extract UI assets into workspace/ui/
    n_ui = extract_package_dir("jenny.templates.ui", workspace / "ui")
    added.append(f"ui/ ({n_ui} files)")
    # Il codice che il package non spedisce piu' (un file rinominato lascia la
    # copia col nome vecchio, e il gateway la servirebbe ancora).
    from jenny.utils.android_assets import retire_withdrawn_ui_files

    try:
        retire_withdrawn_ui_files(workspace / "ui")
    except Exception:  # noqa: BLE001 — una pulizia mancata non ferma la sync
        logger.opt(exception=True).error("Could not remove withdrawn UI files")

    # Extract skills/ into workspace/skills
    skills_dest = workspace / "skills"
    n = extract_package_dir("jenny.skills", skills_dest)
    added.append(f"skills/ ({n} files)")

    # Ensure memory/history.jsonl exists
    mem_dir = workspace / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    history = mem_dir / "history.jsonl"
    if not history.exists():
        history.write_text("", encoding="utf-8")
        added.append("memory/history.jsonl")

    # La cartella dei risultati dell'agente deve esistere *prima* del primo
    # turno: se la trova già lì la usa, se deve crearla sceglie la radice del
    # workspace e ci lascia il file accanto ai documenti di bootstrap.
    # Import locale obbligato: jenny.config.paths importa questo modulo a
    # livello di modulo (ensure_dir), quindi in testa al file sarebbe un ciclo.
    from jenny.config.paths import OUTPUT_SUBDIR, get_output_path

    output_existed = (workspace / OUTPUT_SUBDIR).is_dir()
    get_output_path(workspace, create=True)
    if not output_existed:
        added.append(f"{OUTPUT_SUBDIR}/")

    if added and not silent:
        for name in added:
            logger.info("Created {}", name)

    return added


def load_bundled_template(template_name: str) -> str | None:
    """Read a bundled template file from the jenny package."""
    with suppress(Exception):
        import pkgutil

        data = pkgutil.get_data("jenny", f"templates/{template_name}")
        if data is None:
            return None
        return data.decode("utf-8")
    return None
