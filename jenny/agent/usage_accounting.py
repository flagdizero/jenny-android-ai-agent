"""Contabilità dei token di utilizzo (estratto da AgentRunner).

Normalizzazione/accumulo dell'usage riportato dal provider e stima quando il
provider non lo fornisce. Le funzioni "pure" non dipendono da nulla; quelle di
stima ricevono il ``provider`` esplicitamente (nessuno stato nascosto).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jenny.utils.helpers import (
    build_assistant_message,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
)

if TYPE_CHECKING:
    from jenny.agent.runner import AgentRunSpec
    from jenny.providers.base import LLMProvider, LLMResponse


def usage_dict(usage: dict[str, Any] | None) -> dict[str, int]:
    if not usage:
        return {}
    result: dict[str, int] = {}
    for key, value in usage.items():
        try:
            result[key] = int(value or 0)
        except (TypeError, ValueError):
            continue
    return result


def usage_total(usage: dict[str, int]) -> int:
    return max(
        0,
        usage.get("total_tokens", 0)
        or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)),
    )


def accumulate_usage(target: dict[str, int], addition: dict[str, int]) -> None:
    for key, value in addition.items():
        target[key] = target.get(key, 0) + value


def merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    merged = dict(left)
    for key, value in right.items():
        merged[key] = merged.get(key, 0) + value
    return merged


def usage_or_estimate(
    provider: "LLMProvider",
    spec: "AgentRunSpec",
    messages: list[dict[str, Any]],
    response: "LLMResponse",
) -> dict[str, int]:
    usage = usage_dict(response.usage)
    total = usage_total(usage)
    if total > 0:
        usage["total_tokens"] = total
        usage.setdefault("provider_tokens", total)
        calibrate(spec, messages, usage.get("prompt_tokens", 0))
        return usage
    if response.finish_reason == "error":
        return {}
    return estimate_response_usage(provider, spec, messages, response)


def estimate_response_usage(
    provider: "LLMProvider",
    spec: "AgentRunSpec",
    messages: list[dict[str, Any]],
    response: "LLMResponse",
) -> dict[str, int]:
    try:
        tools = spec.tools.get_definitions()
    except Exception:
        tools = None
    prompt_tokens, _ = estimate_prompt_tokens_chain(provider, spec.model, messages, tools)
    assistant_message = build_assistant_message(
        response.content or "",
        tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
        reasoning_content=response.reasoning_content,
        thinking_blocks=response.thinking_blocks,
    )
    completion_tokens = estimate_message_tokens(assistant_message)
    total_tokens = max(0, prompt_tokens) + max(0, completion_tokens)
    if total_tokens <= 0:
        return {}
    return {
        "prompt_tokens": max(0, prompt_tokens),
        "completion_tokens": max(0, completion_tokens),
        "total_tokens": total_tokens,
        "estimated_tokens": total_tokens,
    }


def calibrate(spec: "AgentRunSpec", messages: list[dict[str, Any]], real_prompt_tokens: int) -> None:
    """Il provider ha contato *real_prompt_tokens*: impara quanto sbaglia la stima.

    Si confronta con la stima **grezza** della stessa richiesta (``messages`` e'
    quel che e' partito, dopo tagli e compattazioni), non con quella corretta,
    altrimenti il rapporto inseguirebbe se stesso. Isolato: una misura che non
    riesce non deve toccare il giro.
    """
    if real_prompt_tokens <= 0:
        return
    try:
        from jenny.utils.helpers import estimate_prompt_tokens
        from jenny.utils.token_calibration import record

        try:
            tools = spec.tools.get_definitions()
        except Exception:
            tools = None
        record(spec.model, real=real_prompt_tokens,
               estimated=estimate_prompt_tokens(messages, tools),
               session_key=spec.session_key)
    except Exception:  # noqa: BLE001 — la misura e' un di piu'
        from loguru import logger

        logger.opt(exception=True).debug("token calibration skipped")
