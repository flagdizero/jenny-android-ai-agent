"""Governo del contesto: rilevamento degli errori di context-length + snip della
history al budget di contesto (estratto da AgentRunner).

Contiene i classificatori PURI degli errori di overflow del contesto (pattern
regex + rilevamento) e ``snip_history`` — il trimming della conversazione al
budget di token del modello (Fase 2.3, con caratterizzazione in
``tests/agent/test_runner_governance.py``). Il loop di iterazione e la recovery
context-limit restano in ``AgentRunner._run_core``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.utils.helpers import (
    CONTEXT_BUDGET_SAFETY_BUFFER,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    find_legal_message_start,
    reserved_output_tokens,
)

if TYPE_CHECKING:
    from jenny.agent.runner import AgentRunSpec
    from jenny.providers.base import LLMProvider, LLMResponse

# OpenAI-compat: "maximum context length is 8192 tokens".
CONTEXT_LIMIT_PATTERN = re.compile(
    r"maximum context length is\s+(\d[\d,]*)\s+tokens", re.IGNORECASE
)

# Anthropic: "prompt is too long: 250000 tokens > 200000 maximum".
ANTHROPIC_CONTEXT_LIMIT_PATTERN = re.compile(
    r"prompt is too long:\s*\d[\d,]*\s+tokens\s*>\s*(\d[\d,]*)\s+maximum", re.IGNORECASE
)


def is_context_length_error(response: "LLMResponse") -> bool:
    """Return True if the response indicates a context_length_exceeded error."""
    if response.finish_reason != "error":
        return False
    kind = (response.error_kind or "").lower()
    if "context_length" in kind:
        return True
    error_type = (response.error_type or "").lower()
    if "context_length" in error_type:
        return True
    error_code = (response.error_code or "").lower()
    if "context_length" in error_code:
        return True
    content = (response.content or "").lower()
    if "context_length_exceeded" in content or "maximum context length" in content:
        return True
    # Anthropic's real 400 invalid_request_error overflow shape; the type alone
    # is too broad, so require the specific "prompt is too long" wording too.
    return error_type == "invalid_request_error" and "prompt is too long" in content


def extract_context_limit(response: "LLMResponse") -> int | None:
    """Try to extract the model's actual context limit from the error message."""
    content = response.content or ""
    match = CONTEXT_LIMIT_PATTERN.search(content)
    if match:
        return int(match.group(1).replace(",", ""))
    match = ANTHROPIC_CONTEXT_LIMIT_PATTERN.search(content)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def snip_history(
    provider: "LLMProvider",
    spec: "AgentRunSpec",
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Trim conversation history to fit the model's context budget.

    Estratto verbatim da ``AgentRunner._snip_history`` (``self.provider`` →
    parametro ``provider``). Preserva i messaggi di sistema, tiene la coda più
    recente entro il budget, e garantisce che la slice inizi con un messaggio
    legale (nessun tool-result orfano) — vedi ``find_legal_message_start``.
    """
    if not messages or not spec.context_window_tokens:
        return messages

    max_output = reserved_output_tokens(provider, spec.max_tokens)
    budget = spec.context_block_limit or (
        spec.context_window_tokens - max_output - CONTEXT_BUDGET_SAFETY_BUFFER
    )
    if budget <= 0:
        return messages

    estimate, _ = estimate_prompt_tokens_chain(
        provider,
        spec.model,
        messages,
        spec.tools.get_definitions(),
    )
    if estimate <= budget:
        return messages

    system_messages = [dict(msg) for msg in messages if msg.get("role") == "system"]
    non_system = [dict(msg) for msg in messages if msg.get("role") != "system"]
    if not non_system:
        return messages

    # Le stime per messaggio nella stessa unita' del cancello, che e' corretto
    # col rapporto misurato sul provider (``token_calibration``).
    from jenny.utils.token_calibration import factor

    scale = factor(spec.model)
    system_tokens = int(sum(estimate_message_tokens(msg) for msg in system_messages) * scale)
    fixed_tokens, _ = estimate_prompt_tokens_chain(
        provider,
        spec.model,
        system_messages,
        spec.tools.get_definitions(),
    )
    remaining_budget = max(0, budget - max(system_tokens, fixed_tokens))
    kept: list[dict[str, Any]] = []
    kept_tokens = 0
    for message in reversed(non_system):
        msg_tokens = int(estimate_message_tokens(message) * scale)
        if kept and kept_tokens + msg_tokens > remaining_budget:
            break
        kept.append(message)
        kept_tokens += msg_tokens
    kept.reverse()

    if kept:
        for i, message in enumerate(kept):
            if message.get("role") == "user":
                kept = kept[i:]
                break
        else:
            # Recover nearest user message from outside the kept window;
            # GLM rejects system→assistant (error 1214).  Budget is
            # intentionally exceeded — oversized beats invalid.
            for idx in range(len(non_system) - 1, -1, -1):
                if non_system[idx].get("role") == "user":
                    kept = non_system[idx:]
                    break
            # If no user exists at all, _enforce_role_alternation
            # will insert a synthetic one as a safety net.
        start = find_legal_message_start(kept)
        if start:
            kept = kept[start:]
    if not kept:
        kept = non_system[-min(len(non_system), 4) :]
        start = find_legal_message_start(kept)
        if start:
            kept = kept[start:]
    # Il taglio e' la rete di sicurezza: se scatta, la compattazione e' rimasta
    # indietro e il modello perde messaggi che nessun riassunto racconta.
    if len(kept) < len(non_system):
        logger.warning(
            "Context snip for {}: {} of {} history messages dropped (estimate {} > budget {})",
            spec.session_key or "?", len(non_system) - len(kept), len(non_system), estimate,
            budget,
        )
    return system_messages + kept
