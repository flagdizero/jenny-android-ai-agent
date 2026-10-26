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


def input_budget(provider: "LLMProvider", spec: "AgentRunSpec") -> int:
    """I token di input che una richiesta puo' usare: la finestra meno l'output riservato.

    Lo stesso conto per lo snip e per la compattazione a meta' turno
    (``agent/inflight_compaction.py``), che devono misurare contro lo stesso tetto.
    ``0`` quando la finestra non e' dichiarata.
    """
    if not spec.context_window_tokens:
        return 0
    max_output = reserved_output_tokens(provider, spec.max_tokens)
    return spec.context_block_limit or (
        spec.context_window_tokens - max_output - CONTEXT_BUDGET_SAFETY_BUFFER
    )


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

    budget = input_budget(provider, spec)
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


def closed_reasoning_can_be_blank(provider: Any, model: str | None) -> bool:
    """Il provider accetta ``reasoning_content: ""`` sui turni chiusi?"""
    check = getattr(provider, "closed_reasoning_can_be_blank", None)
    if not callable(check):
        return False
    try:
        return bool(check(model))
    except Exception:  # noqa: BLE001 — nel dubbio si manda tutto, come prima
        return False


def blank_closed_reasoning(
    messages: list[dict[str, Any]], *, flagged_only: bool = False,
) -> list[dict[str, Any]]:
    """Svuota il ragionamento dei turni gia' chiusi, solo nella richiesta.

    Un turno e' chiuso quando l'assistente ha risposto senza chiamare tool: da
    li' indietro, il ragionamento non serve piu' al modello. Il confine e'
    l'**ultimo assistente senza** ``tool_calls``, non l'ultimo ``user``: un
    messaggio iniettato a meta' giro e l'annuncio di un aiutante sono ``user``
    anche loro, e tagliare li' toglierebbe il ragionamento della catena di tool
    in corso, che DeepSeek vuole indietro (400 senza).

    Misurato il 07/10/2026 su ``opencode-go``/``deepseek-v4.1-flash``: un
    ``""`` sui turni chiusi e' accettato, e quei token non si pagano piu'. Nella
    sessione ETF del Titan i ragionamenti erano il 56% del prompt. I messaggi
    toccati sono copie: la sessione salvata resta intera.

    Con *flagged_only* si svuotano solo le righe marcate da
    :func:`settle_closed_reasoning`, cioe' i turni del loop principale: il
    confine qui sopra si sposta a ogni turno, e il turno dopo rimandava vuoto
    quel che il turno prima aveva mandato pieno, perdendo la cache da li'.
    """
    if flagged_only:
        from jenny.session.history_meta import REASONING_DROPPED_META

        if not any(m.get(REASONING_DROPPED_META) and m.get("reasoning_content")
                   for m in messages):
            return messages
        return [
            {**m, "reasoning_content": ""}
            if m.get(REASONING_DROPPED_META) and m.get("reasoning_content") else m
            for m in messages
        ]
    last_final = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") == "assistant" and not m.get("tool_calls"):
            last_final = i
            break
    if last_final is None:
        return messages
    out = list(messages)
    for i in range(last_final + 1):
        m = out[i]
        if m.get("role") == "assistant" and m.get("reasoning_content"):
            out[i] = {**m, "reasoning_content": ""}
    return out


#: Quanto ragionamento dei turni chiusi si lascia andare al provider prima di
#: azzerarlo comunque, in caratteri (~8k token). Tenerlo costa una lettura in
#: cache a ogni richiesta, azzerarlo costa una volta la catena dopo il punto
#: azzerato: oltre questa soglia la lettura costa di piu'. Il 07/10/2026, senza
#: tetto, i ragionamenti erano il 56% del prompt di una sessione del Titan.
CLOSED_REASONING_KEEP_CHARS = 32_000

_CACHE_FINGERPRINT_KEY = "_closed_reasoning_fingerprint"


def settle_closed_reasoning(
    rows: list[dict[str, Any]],
    built: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> bool:
    """Marca il ragionamento dei turni chiusi da non rimandare, ma solo quando costa poco.

    *rows* sono i messaggi della sessione, *built* la richiesta appena costruita
    (system, storia, messaggio corrente), *metadata* quelli della sessione. Si
    marca tutto il ragionamento chiuso — su *rows*, per i turni dopo, e sulle
    righe di storia di *built*, per questo — quando:

    - la cache di questo turno e' gia' persa: il system prompt o la prima riga
      della finestra sono cambiati dal turno prima (un consolidamento, Dream, la
      finestra che scorre). L'impronta sta in *metadata*; la prima volta manca,
      e vale come cambiata;
    - oppure il ragionamento non ancora marcato supera
      :data:`CLOSED_REASONING_KEEP_CHARS`.

    Altrimenti non si tocca niente, e il turno dopo rimanda gli stessi byte.
    Ritorna ``True`` se ha marcato.
    """
    import hashlib

    from jenny.session.history_meta import REASONING_DROPPED_META

    head = built[:2]
    fingerprint = hashlib.sha256(
        repr([(m.get("role"), m.get("content")) for m in head]).encode("utf-8", "replace"),
    ).hexdigest()
    changed = metadata.get(_CACHE_FINGERPRINT_KEY) != fingerprint
    metadata[_CACHE_FINGERPRINT_KEY] = fingerprint
    pending = [
        m for m in rows
        if m.get("role") == "assistant" and m.get("reasoning_content")
        and not m.get(REASONING_DROPPED_META)
    ]
    if not pending:
        return False
    size = sum(len(str(m["reasoning_content"])) for m in pending)
    if not changed and size <= CLOSED_REASONING_KEEP_CHARS:
        return False
    for m in pending:
        m[REASONING_DROPPED_META] = True
    for m in built[1:]:
        if m.get("role") == "assistant" and m.get("reasoning_content"):
            m[REASONING_DROPPED_META] = True
    return True


MID_TURN_LABEL = ("[New message from the user while you were working. Answer it before going "
                  "on with what you were doing.]")


def label_mid_turn_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Presenta come tale un messaggio scritto mentre il turno lavorava.

    Iniettato a meta' giro, arrivava al modello come un ``user`` qualunque in
    mezzo a una catena di tool, e il modello lo mescolava al compito in corso:
    sul Titan, il 07/10/2026, «puoi lanciare l'aggiornamento a mano?» chiesto tre
    volte e risposto alla fine di un altro lavoro. L'etichetta sta solo nella
    richiesta: il transcript resta come l'utente l'ha scritto.
    """
    from jenny.session.history_meta import MID_TURN_META

    out = messages
    for i, m in enumerate(messages):
        if m.get("role") != "user" or not m.get(MID_TURN_META):
            continue
        if out is messages:
            out = list(messages)
        content = m.get("content")
        if isinstance(content, str):
            labelled: Any = f"{MID_TURN_LABEL}\n{content}"
        elif isinstance(content, list):
            labelled = [{"type": "text", "text": MID_TURN_LABEL}, *content]
        else:
            continue
        out[i] = {**m, "content": labelled}
    return out


def last_helper_result(messages: list[dict[str, Any]]) -> Any:
    """Il contenuto dell'ultimo risultato d'aiutante, se è l'ultima riga ``user``.

    È il candidato del recovery di un rifiuto del provider
    (``AgentRunner._recover_provider_refusal``): misurato sul telefono, il
    ``content_filter`` arrivava subito dopo il rientro di un aiutante. Si guarda
    l'ultima riga ``user`` saltando assistant e tool; se è un messaggio vero
    dell'utente (o un'altra riga sintetica) non c'è niente da togliere, e torna
    ``None``. Il rientro si riconosce dal marcatore ``injected_event``, che il
    loop mette sia sull'iniezione a metà turno (``AgentLoop._drain_pending``)
    sia sul messaggio corrente di un turno d'annuncio.
    """
    from jenny.session.history_meta import INJECTED_EVENT_META, SUBAGENT_RESULT_EVENT

    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        if m.get(INJECTED_EVENT_META) != SUBAGENT_RESULT_EVENT:
            return None
        content = m.get("content")
        return content if content else None
    return None


def mask_refused_results(
    messages: list[dict[str, Any]], refused: list[Any]
) -> list[dict[str, Any]]:
    """Mette il segnaposto al posto dei risultati rifiutati, solo nella richiesta.

    *refused* sono i contenuti tolti da questo run (``last_helper_result``):
    una riga ``user`` con lo stesso contenuto diventa
    ``PROVIDER_REFUSED_RESULT_PLACEHOLDER``. Per contenuto e non per identità:
    il governo del contesto copia i dict a ogni iterazione. Il blocco del
    Runtime Context, se la riga lo porta (il messaggio corrente di un turno),
    resta: è lì che il modello legge il piano e l'ora. La storia non cambia.
    """
    if not refused:
        return messages
    from jenny.agent.context import ContextBuilder
    from jenny.utils.runtime import PROVIDER_REFUSED_RESULT_PLACEHOLDER

    placeholder = PROVIDER_REFUSED_RESULT_PLACEHOLDER
    tag = ContextBuilder._RUNTIME_CONTEXT_TAG
    out = messages
    for i, m in enumerate(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if not content or content not in refused:
            continue
        if out is messages:
            out = list(messages)
        masked: Any
        if isinstance(content, str):
            pos = content.find(tag)
            masked = placeholder if pos < 0 else f"{placeholder}\n\n{content[pos:]}"
        elif isinstance(content, list):
            masked = [{"type": "text", "text": placeholder}] + [
                block for block in content
                if isinstance(block, dict)
                and isinstance(block.get("text"), str)
                and block["text"].startswith(tag)
            ]
        else:
            masked = placeholder
        out[i] = {**m, "content": masked}
    return out
