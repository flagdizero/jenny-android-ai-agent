"""Conversione messaggi/tool per l'``AnthropicProvider`` (estratta).

`AnthropicConversionMixin` traduce il formato messaggi interno nel wire-format
Anthropic (blocchi tool-use/result, immagini, merge di ruoli consecutivi,
tools/tool_choice, cache_control) e genera/sanitizza i tool-id. Metodi
statici/classe: risolti per MRO in ``AnthropicProvider``. Nessun ciclo.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
from collections.abc import Iterable
from typing import Any

from jenny.providers.base import tool_arguments_object_for_replay
from jenny.providers.message_repair import SYNTHETIC_USER_CONTENT

_ALNUM = string.ascii_letters + string.digits


def _gen_tool_id() -> str:
    return "toolu_" + "".join(secrets.choice(_ALNUM) for _ in range(22))


def derive_tool_id(seed: str, idx: int, salt: int = 1) -> str:
    """Id sostitutivo per una tool call il cui id è già occupato.

    Deterministico di proposito: la stessa history deve produrre gli stessi id a
    ogni richiesta. Con id casuali il prefisso della conversazione cambierebbe a
    ogni retry e il prompt caching — che qui è sempre attivo, vedi
    ``_apply_cache_control`` — ripartirebbe da zero ogni volta.
    """
    digest = hashlib.sha1(f"{seed}:{idx}:{salt}".encode()).hexdigest()
    return f"toolu_{digest[:22]}"


def replayable_thinking_blocks(blocks: Iterable[Any]) -> list[dict[str, Any]]:
    """Filtra i blocchi thinking che si possono rimandare all'API.

    Un blocco ``thinking`` vale solo con la sua firma: l'API rifiuta quelli non
    firmati, quindi uno senza firma va scartato, non inviato — scartarlo riporta
    al comportamento di prima (nessun blocco), inviarlo sarebbe un 400. Un
    ``redacted_thinking`` non ha testo né firma: porta ``data``, opaco, e va
    ripassato tale e quale.
    """
    result: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking" and block.get("signature"):
            result.append({
                "type": "thinking",
                "thinking": block.get("thinking", ""),
                "signature": block["signature"],
            })
        elif block.get("type") == "redacted_thinking" and block.get("data"):
            result.append({"type": "redacted_thinking", "data": block["data"]})
    return result


_VALID_TOOL_ID = re.compile(r"\A[a-zA-Z0-9_-]+\Z")


def _sanitize_tool_id(tid: str) -> str:
    """Ensure tool_use/tool_result IDs match Anthropic's required pattern.

    The Anthropic API rejects tool IDs that don't match ``^[a-zA-Z0-9_-]+$``
    with a 400 ("String should match pattern") error. IDs coming from other
    providers or restored sessions can contain pipes, dots or other invalid
    characters, so coerce them to the allowed charset.
    """
    if not tid or _VALID_TOOL_ID.match(tid):
        return tid
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", tid)[:48].strip("_") or "toolu"
    digest = hashlib.sha1(tid.encode()).hexdigest()[:8]
    return f"{safe_prefix}_{digest}"



class AnthropicConversionMixin:
    """Conversione formato messaggi Anthropic (mixin del provider)."""

    def _convert_messages(
        self, messages: list[dict[str, Any]],
    ) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]]]:
        """Return ``(system, anthropic_messages)``."""
        system: str | list[dict[str, Any]] = ""
        raw: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            if role == "system":
                system = content if isinstance(content, (str, list)) else str(content or "")
                continue

            if role == "tool":
                block = self._tool_result_block(msg)
                if raw and raw[-1]["role"] == "user":
                    prev_c = raw[-1]["content"]
                    if isinstance(prev_c, list):
                        prev_c.append(block)
                    else:
                        raw[-1]["content"] = [
                            {"type": "text", "text": prev_c or ""}, block,
                        ]
                else:
                    raw.append({"role": "user", "content": [block]})
                continue

            if role == "assistant":
                raw.append({"role": "assistant", "content": self._assistant_blocks(msg)})
                continue

            if role == "user":
                raw.append({
                    "role": "user",
                    "content": self._convert_user_content(content),
                })
                continue

        return system, self._merge_consecutive(raw)

    @staticmethod
    def _tool_result_block(msg: dict[str, Any]) -> dict[str, Any]:
        content = msg.get("content")
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": _sanitize_tool_id(msg.get("tool_call_id", "")),
        }
        if isinstance(content, list):
            block["content"] = AnthropicConversionMixin._convert_user_content(content)
        elif isinstance(content, str):
            block["content"] = content
        else:
            block["content"] = str(content) if content else ""
        return block

    @staticmethod
    def _assistant_blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        content = msg.get("content")

        # I blocchi thinking vanno PRIMA di testo e tool_use: è l'ordine in cui
        # il modello li ha prodotti, ed è quello che l'API si aspetta di
        # riavere. ``replayable_thinking_blocks`` scarta quelli inutilizzabili
        # invece di farli rifiutare dall'API.
        blocks.extend(replayable_thinking_blocks(msg.get("thinking_blocks") or []))

        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for item in content:
                blocks.append(item if isinstance(item, dict) else {"type": "text", "text": str(item)})

        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", {})
            args = func.get("arguments", "{}")
            blocks.append({
                "type": "tool_use",
                "id": _sanitize_tool_id(tc.get("id") or _gen_tool_id()),
                "name": func.get("name", ""),
                "input": tool_arguments_object_for_replay(args),
            })

        # Un blocco text VUOTO è proprio la forma che l'API rifiuta ("text
        # content blocks must be non-empty"): il fallback esiste perché
        # ``content`` deve avere almeno un blocco, quindi deve essere un
        # placeholder, coerente con ``_convert_user_content``.
        return blocks or [{"type": "text", "text": "(empty)"}]

    @staticmethod
    def _convert_user_content(content: Any) -> Any:
        """Convert user message content, translating image_url blocks."""
        if isinstance(content, str) or content is None:
            return content or "(empty)"
        if not isinstance(content, list):
            return str(content)

        result: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                result.append({"type": "text", "text": str(item)})
                continue
            if item.get("type") == "image_url":
                converted = AnthropicConversionMixin._convert_image_block(item)
                if converted:
                    result.append(converted)
                continue
            if not item.get("type"):
                # Anthropic requires every content block to declare a "type".
                # A tool that returned a bare dict (or a list of dicts) lands
                # here; coerce it to a text block instead of emitting a block
                # the API rejects with "content.0.type: Field required".
                result.append({"type": "text", "text": str(item)})
                continue
            result.append(item)
        return result or "(empty)"

    @staticmethod
    def _convert_image_block(block: dict[str, Any]) -> dict[str, Any] | None:
        """Convert OpenAI image_url block to Anthropic image block."""
        url = (block.get("image_url") or {}).get("url", "")
        if not url:
            return None
        m = re.match(r"data:(image/\w+);base64,(.+)", url, re.DOTALL)
        if m:
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)},
            }
        return {
            "type": "image",
            "source": {"type": "url", "url": url},
        }

    @staticmethod
    def _has_tool_use(msg: dict[str, Any]) -> bool:
        """True if ``msg.content`` carries any ``tool_use`` block.

        Anthropic forbids ``tool_use`` inside ``user`` turns, so messages that
        issued a tool call cannot be safely rerouted when we patch the role.
        """
        content = msg.get("content")
        if not isinstance(content, list):
            return False
        return any(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in content
        )

    @staticmethod
    def _merge_consecutive(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize a message sequence for Anthropic's ``/messages`` endpoint.

        Anthropic's contract is stricter than OpenAI's:

        1. Consecutive same-role turns must be collapsed into one.
        2. The conversation cannot end with an ``assistant`` turn — Anthropic
           does not support assistant-message prefill and returns 400.
        3. The conversation cannot start with an ``assistant`` turn — the
           first message must be ``user``.

        Rules 2 and 3 mirror ``LLMProvider._enforce_role_alternation`` in
        ``base.py``, which applies the equivalent invariants to OpenAI-compat
        providers.  The only Anthropic-specific wrinkle: ``tool_use`` blocks
        live inside ``content`` (not a separate ``tool_calls`` field) and are
        invalid inside ``user`` turns, so the recovery paths below must skip
        any message carrying them rather than silently producing a malformed
        request.
        """
        merged: list[dict[str, Any]] = []
        for msg in msgs:
            if merged and merged[-1]["role"] == msg["role"]:
                prev_c = merged[-1]["content"]
                cur_c = msg["content"]
                if isinstance(prev_c, str):
                    prev_c = [{"type": "text", "text": prev_c}]
                if isinstance(cur_c, str):
                    cur_c = [{"type": "text", "text": cur_c}]
                if isinstance(cur_c, list):
                    prev_c.extend(cur_c)
                merged[-1]["content"] = prev_c
            else:
                merged.append(msg)

        # Rule 2: strip trailing assistant turns — Anthropic rejects prefill.
        last_popped: dict[str, Any] | None = None
        while merged and merged[-1].get("role") == "assistant":
            last_popped = merged.pop()

        # Recovery for rule 2: if stripping removed every turn, reroute the
        # last popped assistant as a user turn so upstream code still gets a
        # valid request instead of a secondary "messages array empty" 400.
        # Skip when the message carried ``tool_use`` blocks (see _has_tool_use).
        if (
            not merged
            and last_popped is not None
            and not AnthropicConversionMixin._has_tool_use(last_popped)
        ):
            merged.append({"role": "user", "content": last_popped.get("content")})

        # Rule 3: prepend a synthetic opener if the first surviving turn is an
        # assistant (e.g. upstream history truncation dropped the original
        # user request).  ``tool_use``-carrying assistants are left alone —
        # that message will still fail validation, but injecting an opener
        # before it would orphan the tool_use/tool_result pair that follows,
        # turning a recoverable 400 into a harder-to-diagnose one.
        if (
            merged
            and merged[0].get("role") == "assistant"
            and not AnthropicConversionMixin._has_tool_use(merged[0])
        ):
            merged.insert(0, {"role": "user", "content": SYNTHETIC_USER_CONTENT})

        return merged

    # ------------------------------------------------------------------
    # Tool definition conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        result = []
        for tool in tools:
            func = tool.get("function", tool)
            entry: dict[str, Any] = {
                "name": func.get("name", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            }
            desc = func.get("description")
            if desc:
                entry["description"] = desc
            if "cache_control" in tool:
                entry["cache_control"] = tool["cache_control"]
            result.append(entry)
        return result

    @staticmethod
    def _convert_tool_choice(
        tool_choice: str | dict[str, Any] | None,
        thinking_enabled: bool = False,
    ) -> dict[str, Any] | None:
        if thinking_enabled:
            return {"type": "auto"}
        if tool_choice is None or tool_choice == "auto":
            return {"type": "auto"}
        # ``any`` è il valore nativo Anthropic e uno di quelli che lo schema di
        # config ammette; senza questo ramo cadeva nel default auto.
        if tool_choice in ("required", "any"):
            return {"type": "any"}
        if tool_choice == "none":
            return None
        if isinstance(tool_choice, dict):
            name = tool_choice.get("function", {}).get("name")
            if name:
                return {"type": "tool", "name": name}
        return {"type": "auto"}

    # ------------------------------------------------------------------
    # Prompt caching
    # ------------------------------------------------------------------

    @classmethod
    def _apply_cache_control(
        cls,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]] | None]:
        marker = {"type": "ephemeral"}

        if isinstance(system, str) and system:
            system = [{"type": "text", "text": system, "cache_control": marker}]
        elif isinstance(system, list) and system:
            system = list(system)
            system[-1] = {**system[-1], "cache_control": marker}

        new_msgs = list(messages)
        if len(new_msgs) >= 3:
            m = new_msgs[-2]
            c = m.get("content")
            if isinstance(c, str):
                new_msgs[-2] = {**m, "content": [{"type": "text", "text": c, "cache_control": marker}]}
            elif isinstance(c, list) and c:
                nc = list(c)
                nc[-1] = {**nc[-1], "cache_control": marker}
                new_msgs[-2] = {**m, "content": nc}

        new_tools = tools
        if tools:
            new_tools = list(tools)
            for idx in cls._tool_cache_marker_indices(new_tools):
                new_tools[idx] = {**new_tools[idx], "cache_control": marker}

        return system, new_msgs, new_tools
